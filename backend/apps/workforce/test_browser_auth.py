"""
The authentication path a real browser actually takes.

WHY THIS FILE EXISTS SEPARATELY. Every other API test authenticates with
`force_authenticate`, which installs a user directly and skips both the
credential exchange and DRF's CSRF enforcement. That is the right tool for
testing authorization rules, but it means the suite can be entirely green
while no browser can perform a single action.

The sequence below is the one the frontend performs: sign in, receive a
token AND a session cookie (the WebSocket needs the session, since a browser
cannot set an Authorization header on a socket), then act using the token.
With CSRF checks enforced, that combination is exactly where the
authenticator ORDER matters -- see AUTH_CLASSES in views.py.
"""
from rest_framework.test import APIClient, APITestCase

from . import states
from .tests import make_task, make_worker


class BrowserAuthenticationTestCase(APITestCase):

    def setUp(self):
        # enforce_csrf_checks is the whole point: the default APIClient
        # disables them, which is what let this hide.
        self.client = APIClient(enforce_csrf_checks=True)
        self.user, self.profile = make_worker('ana', 'W-01')
        self.password = 'an-adequately-long-passphrase'

    def _sign_in(self):
        response = self.client.post(
            '/api/v1/workforce/auth/login/',
            {'username': 'ana', 'password': self.password},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['token']

    def test_signing_in_works_without_a_csrf_token(self):
        # An anonymous caller has no session for SessionAuthentication to
        # find, so no CSRF check applies -- otherwise nobody could ever log
        # in for the first time.
        token = self._sign_in()
        self.assertTrue(token)

    def test_signing_in_opens_a_session_as_well_as_issuing_a_token(self):
        self._sign_in()
        # The WebSocket consumer authenticates from this cookie.
        self.assertIn('sessionid', self.client.cookies)

    def test_a_token_authenticated_action_succeeds_after_signing_in(self):
        """
        The regression this file was written for.

        With SessionAuthentication first in AUTH_CLASSES, DRF authenticates
        this request by the session cookie left over from signing in, then
        rejects it for a missing CSRF token -- a 403 on every claim, start,
        ready and submit, from every browser, while the whole test suite
        stayed green.
        """
        token = self._sign_in()
        task = make_task(status=states.NEW)

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        response = self.client.post(f'/api/v1/workforce/tasks/{task.id}/claim/')

        self.assertEqual(response.status_code, 200, getattr(response, 'data', response))
        self.assertEqual(response.data['status'], states.CLAIMED)

    def test_a_read_also_works_with_the_token(self):
        token = self._sign_in()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token}')

        response = self.client.get('/api/v1/workforce/me/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], 'ana')

    def test_a_bad_token_is_rejected_rather_than_falling_back_to_the_session(self):
        """
        A garbled token must fail, not quietly authenticate as whoever the
        session cookie belongs to. Otherwise a stale token would appear to
        work and mask the real problem.
        """
        self._sign_in()
        self.client.credentials(HTTP_AUTHORIZATION='Token not-a-real-token')

        response = self.client.get('/api/v1/workforce/me/')

        self.assertEqual(response.status_code, 401)

    def test_signing_out_invalidates_the_token(self):
        token = self._sign_in()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token}')

        self.assertEqual(
            self.client.post('/api/v1/workforce/auth/logout/').status_code, 204,
        )
        # The token is deleted server-side, not merely forgotten by the
        # browser -- a logout that leaves a working credential behind has
        # not logged anything out.
        self.assertEqual(self.client.get('/api/v1/workforce/me/').status_code, 401)

    def test_an_unauthenticated_action_is_refused(self):
        task = make_task(status=states.NEW)

        response = self.client.post(f'/api/v1/workforce/tasks/{task.id}/claim/')

        self.assertIn(response.status_code, (401, 403))


class OriginCheckTestCase(APITestCase):
    """
    Signing in must not depend on a session the caller already has.

    THE BUG THIS PINS SHUT. Django checks the `Origin` header against the
    request's host on every unsafe request that reaches its CSRF machinery.
    In development the browser's origin is the Vite dev server while the
    request arrives at Django on another port, so the two never match on
    their own. With SessionAuthentication on the login endpoint, a stale
    session cookie made DRF authenticate the request BEFORE reading the
    credentials, then enforce CSRF on it -- so a person signing in again
    after an earlier session got:

        CSRF Failed: Origin checking failed - http://localhost:3000 does
        not match any trusted origins.

    Baffling, and nothing to do with their password. Two fixes, both
    covered here: the login endpoint declares no authenticators, and the
    dev origins are trusted for the paths that legitimately use a session.
    """

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.user, self.profile = make_worker('ana', 'W-01')
        self.password = 'an-adequately-long-passphrase'
        self.url = '/api/v1/workforce/auth/login/'
        self.body = {'username': 'ana', 'password': self.password}

    def test_signing_in_twice_in_a_row_works(self):
        """The exact sequence a person performs: sign in, come back, sign in."""
        first = self.client.post(self.url, self.body, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertIn('sessionid', self.client.cookies)

        # The session cookie from the first call is now sent with the second.
        second = self.client.post(self.url, self.body, format='json')

        self.assertEqual(second.status_code, 200, getattr(second, 'data', second))

    def test_signing_in_works_with_a_cross_port_origin_header(self):
        # What a browser on the Vite dev server actually sends.
        response = self.client.post(
            self.url, self.body, format='json', HTTP_ORIGIN='http://localhost:3000',
        )

        self.assertEqual(response.status_code, 200, getattr(response, 'data', response))

    def test_repeat_sign_in_with_an_origin_header_also_works(self):
        self.client.post(self.url, self.body, format='json',
                         HTTP_ORIGIN='http://localhost:3000')

        response = self.client.post(self.url, self.body, format='json',
                                    HTTP_ORIGIN='http://localhost:3000')

        self.assertEqual(response.status_code, 200, getattr(response, 'data', response))
        # Belt and braces: the failure mode was a 403 carrying this phrase.
        self.assertNotIn('CSRF', str(getattr(response, 'data', '')))

    def test_the_dev_origins_are_trusted_for_both_hostnames(self):
        """
        `localhost` and `127.0.0.1` are different origins to a browser, with
        separate cookies and separate localStorage. Whichever one someone
        types has to work; testing only one is how this was missed.
        """
        from django.conf import settings

        for origin in ('http://localhost:3000', 'http://127.0.0.1:3000'):
            with self.subTest(origin=origin):
                self.assertIn(origin, settings.CSRF_TRUSTED_ORIGINS)

    def test_the_login_endpoint_declares_no_authenticators(self):
        # Asserting the property directly, because it is the fix: an
        # endpoint whose job is to establish identity must not read one.
        from .views import LoginView

        self.assertEqual(list(LoginView.authentication_classes), [])
