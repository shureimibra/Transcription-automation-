"""
Rate limiting, proved here because it is switched off everywhere else.

WHY IT IS OFF IN THE REST OF THE SUITE. Throttling keys on the client
address and every test shares one, so a few hundred logins trip the limit
and start failing tests that have nothing to do with rate limiting. Worse,
which tests fail moves as tests are added.

So the suite runs without it and this file turns it back on deliberately,
one scope at a time. The rule being followed: a protection disabled for
convenience has to be demonstrated somewhere, or "we have rate limiting"
becomes a claim nobody has checked.

WHAT IT IS PROTECTING. Every scoped endpoint spends something that does not
come back -- Gemini quota, Eagle minutes, bandwidth from the platform we
depend on. With no limit at all, one retry loop in a browser tab can drain
a day's allowance before anybody notices, and the first symptom is warming
quietly failing for everyone.
"""
from django.contrib.auth.models import User
from django.core.cache import cache
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.settings import api_settings
from rest_framework.test import APIClient

THROTTLED = {
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
        'rest_framework.throttling.ScopedRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/min', 'user': '600/min',
        'login': '3/min',        # deliberately tiny, to be reachable here
        'ai': '30/hour', 'media': '120/hour', 'submit': '60/hour',
    },
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.AllowAny'],
    'DEFAULT_AUTHENTICATION_CLASSES': [],
}

LOGIN = '/api/v1/workforce/auth/login/'


class LoginThrottleTestCase(TestCase):
    """
    The one endpoint anybody on the internet can reach, so the one that
    gets guessed at.

    OVERRIDING THE SETTING IS NOT ENOUGH, which cost a round to discover.
    `APIView.throttle_classes` defaults to `api_settings.DEFAULT_THROTTLE_
    CLASSES` and is bound when the CLASS is created, so a view imported
    before the override keeps the list it was born with. The class attribute
    has to be patched as well -- and a test that overrode only the setting
    passed six unthrottled requests while appearing to test throttling.
    """

    def setUp(self):
        # Throttle state lives in the cache and outlives a test otherwise,
        # which makes the SECOND test in a run fail for the first one's
        # reasons.
        cache.clear()
        User.objects.create_user('ana', password='right-password-2026')
        self.client = APIClient()

        from rest_framework.throttling import ScopedRateThrottle
        from apps.workforce.views import LoginView

        patcher = mock.patch.object(
            LoginView, 'throttle_classes', [ScopedRateThrottle])
        patcher.start()
        self.addCleanup(patcher.stop)

        # AND THE RATES ARE A CLASS ATTRIBUTE TOO. SimpleRateThrottle binds
        # THROTTLE_RATES from api_settings at import, so overriding the
        # setting alone leaves "No default throttle rate set for 'login'".
        from rest_framework.throttling import SimpleRateThrottle

        rates = mock.patch.object(
            SimpleRateThrottle, 'THROTTLE_RATES', {'login': '3/min'})
        rates.start()
        self.addCleanup(rates.stop)

    def tearDown(self):
        cache.clear()

    def _attempt(self, password='wrong-password-2026'):
        return self.client.post(
            LOGIN, {'username': 'ana', 'password': password}).status_code

    def test_repeated_guesses_are_cut_off(self):
        seen = [self._attempt() for _ in range(6)]

        self.assertIn(429, seen, f'never throttled: {seen}')
        # And it stops EARLY, not after the wordlist has been worked
        # through: the limit is 3, so the fourth attempt is the first
        # refusal.
        self.assertEqual(seen.index(429), 3, seen)

    def test_the_right_password_is_still_refused_once_throttled(self):
        """
        THE POINT OF THROTTLING, and the thing that makes it worth the
        inconvenience: a correct guess arriving after the limit does not
        get in either.
        """
        for _ in range(4):
            self._attempt()

        self.assertEqual(self._attempt('right-password-2026'), 429)

    def test_a_person_who_simply_mistypes_is_not_locked_out(self):
        # Two fat-fingered attempts then the real password must work, or
        # the protection costs more than it saves.
        self._attempt()
        self._attempt()

        self.assertEqual(self._attempt('right-password-2026'), 200)


class ThrottleConfigurationTestCase(TestCase):
    """The rates themselves, which are a judgement rather than a mechanism."""

    def test_every_expensive_endpoint_has_a_scope(self):
        """
        A scope that is configured but never attached to a view throttles
        nothing -- the same shape of mistake as the SSRF check that was
        only ever called from its own tests.
        """
        from apps.workforce.views import LoginView
        from apps.workforce.worker_views import SubmitToEagleView, TaskMediaView

        self.assertEqual(LoginView.throttle_scope, 'login')
        self.assertEqual(TaskMediaView.throttle_scope, 'media')
        self.assertEqual(SubmitToEagleView.throttle_scope, 'submit')

    def test_the_configured_scopes_all_have_rates(self):
        from django.conf import settings

        # Read THROTTLE_RATES, not REST_FRAMEWORK: the suite runs with
        # throttling switched off, which empties the dict inside
        # REST_FRAMEWORK. The named constant keeps the deployed numbers
        # readable, which is the whole reason it has a name.
        import importlib
        module = importlib.import_module(settings.SETTINGS_MODULE)
        rates = module.THROTTLE_RATES

        for scope in ('anon', 'user', 'login', 'ai', 'media', 'submit'):
            self.assertIn(scope, rates)
            self.assertTrue(rates[scope], f'{scope} has an empty rate')

    def test_the_login_rate_allows_for_a_shared_office_address(self):
        """
        THROTTLING KEYS ON AN IP, NOT A PERSON. Six taskers behind one NAT
        share this budget, so a rate tuned for one of them locks out the
        other five -- and "I cannot sign in" looks nothing like its cause.
        """
        from django.conf import settings
        import importlib

        module = importlib.import_module(settings.SETTINGS_MODULE)
        rate = module.THROTTLE_RATES['login']
        count, _, period = rate.partition('/')

        self.assertEqual(period, 'min')
        self.assertGreaterEqual(int(count), 20, 'too tight for a shared address')


class BrowsableAPIRendererTestCase(TestCase):
    """
    DRF's own default renderer list includes BrowsableAPIRenderer
    unconditionally -- not gated by DEBUG -- so leaving REST_FRAMEWORK's
    DEFAULT_RENDERER_CLASSES unset meant every endpoint rendered an
    interactive HTML debugging UI, with live forms for every unsafe
    method, to anyone who asked for it with Accept: text/html, in
    production exactly as in development. Permission checks still apply
    underneath, so this never granted access to data a JSON request could
    not also reach -- but it handed out the whole API's shape to anyone
    probing with a browser, for no benefit once deployed.
    """

    def test_json_rendering_is_always_available(self):
        from django.conf import settings
        renderers = settings.REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES']
        self.assertIn('rest_framework.renderers.JSONRenderer', renderers)

    def test_the_browsable_api_tracks_development_exactly(self):
        # Environment-agnostic on purpose: this passes whether the suite
        # happens to run with DEVELOPMENT True or False, because it checks
        # that the two AGREE rather than asserting one hardcoded answer.
        from django.conf import settings
        renderers = settings.REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES']
        has_browsable = 'rest_framework.renderers.BrowsableAPIRenderer' in renderers
        self.assertEqual(has_browsable, settings.DEVELOPMENT)
