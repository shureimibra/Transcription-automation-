"""
The Eagle integration, tested without touching Eagle.

WHAT THESE PROTECT. Three things that are expensive to get wrong and easy to
regress:

  * A stored session must not be readable from a database dump, and must
    never appear in a log line, a serializer or an exception message.
  * Session expiry must be detected by SHAPE, not status code. Translation
    answers `HTTP 200 {"user": null}` when logged out and transcription
    redirects to the marketing site -- a naive "did it 200?" check reads both
    as success, which is the mistake the first research probe actually made.
  * The credential-bearing form of a media reference must be reachable only
    through the door named for it.

No test here makes a network call. The HTTP layer is substituted, because the
point is our logic, and because claiming a real task spends real money.
"""
import io
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from core.services.eagle_client import (
    merge_set_cookies,
    xsrf_header,
    EagleAccount, EagleLoginRejected, EagleSessionExpired, RawTask,
    TranscriptionClient, TranslationClient,
)

from .credentials import EAGLE_LOGIN_COOLDOWN_SECONDS, TRANSCRIPTION, TRANSLATION, EagleCredential
from .eagle import EagleAuthError, EagleLoginFailed, EagleNotConfigured
from .eagle_impl import EagleService

COOKIE = 'translatepro_session=SUPERSECRET; cf_clearance=ALSOSECRET'

#: A fixture value, never a real credential. Every "never leaks" test below
#: greps output/exceptions/logs for this exact string.
FAKE_PASSWORD = 'not-a-real-password-only-a-test-fixture-88213'
FAKE_USERNAME = 'shurz@example.com'


def store(product, cookie=COOKIE, status=EagleCredential.ACTIVE):
    credential = EagleCredential(product=product)
    # set_cookie() resets status to UNKNOWN by design -- a new cookie is
    # unproven until verified -- so the status has to be applied AFTER it,
    # not before. Getting this backwards made a test that meant to assert
    # "refuses when expired" instead reach the live network.
    credential.set_cookie(cookie)
    credential.status = status
    credential.save()
    return credential


def store_login(product, username=FAKE_USERNAME, password=FAKE_PASSWORD):
    """Login credentials only, no cookie -- the fresh-deployment shape."""
    credential = EagleCredential(product=product)
    credential.set_login(username, password)
    credential.save()
    return credential


def store_with_login(product, cookie=COOKIE, username=FAKE_USERNAME,
                      password=FAKE_PASSWORD, status=EagleCredential.ACTIVE):
    """Both a cookie and a login -- the steady-state shape the retry tests
    need: a working session PLUS a fallback way to obtain a new one."""
    credential = EagleCredential(product=product)
    credential.set_cookie(cookie)
    credential.set_login(username, password)
    credential.status = status
    credential.save()
    return credential


class NoNetworkMixin:
    """
    A hard stop between this test module and the internet.

    One test in this file reached the real Eagle server because a helper set
    a field in the wrong order, and it failed with a genuine 401 from a
    third party rather than the refusal it was asserting. Tests should fail
    for their own reasons; a suite that can silently call out is slow,
    flaky, and rude to someone else's servers.

    Any test needing a canned response patches at a higher level -- the
    client method -- so nothing legitimate is blocked by this.
    """

    def setUp(self):
        super().setUp()
        patcher = mock.patch(
            'core.services.eagle_client._request',
            side_effect=AssertionError(
                'This test tried to make a real HTTP request to Eagle. '
                'Patch the client method instead.'
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)


# ===================================================================== #
# The credential at rest
# ===================================================================== #

class CredentialStorageTestCase(NoNetworkMixin, TestCase):

    def test_the_cookie_is_not_stored_in_clear_text(self):
        credential = store(TRANSLATION)

        blob = bytes(EagleCredential.objects.get(pk=credential.pk).cookie_encrypted)

        self.assertNotIn(b'SUPERSECRET', blob)
        self.assertNotIn(b'ALSOSECRET', blob)
        self.assertNotIn(b'translatepro_session', blob)

    def test_it_round_trips(self):
        store(TRANSLATION)
        self.assertEqual(
            EagleCredential.objects.get(product=TRANSLATION).get_cookie_header(), COOKIE,
        )

    def test_str_carries_no_cookie_material(self):
        # __str__ ends up in log lines; that is the whole risk.
        rendered = str(store(TRANSLATION))
        self.assertNotIn('SUPERSECRET', rendered)
        self.assertNotIn('cf_clearance', rendered)

    def test_an_empty_cookie_is_refused(self):
        credential = EagleCredential(product=TRANSLATION)
        with self.assertRaises(ValueError):
            credential.set_cookie('   ')

    def test_an_undecryptable_blob_reads_as_absent_rather_than_exploding(self):
        # The realistic cause is a rotated key. A poll must degrade to "not
        # configured", not 500 halfway through.
        credential = store(TRANSLATION)
        credential.cookie_encrypted = b'not-a-fernet-token'
        credential.save(update_fields=['cookie_encrypted'])

        self.assertEqual(EagleCredential.objects.get(pk=credential.pk).get_cookie_header(), '')

    def test_replacing_the_cookie_resets_verification(self):
        credential = store(TRANSLATION)
        credential.mark_verified(account_label='Shurz', minutes_balance=15)
        self.assertEqual(credential.status, EagleCredential.ACTIVE)

        credential.set_cookie('a=b')

        # A new cookie is unproven until the health check says otherwise.
        self.assertEqual(credential.status, EagleCredential.UNKNOWN)

    def test_marking_expired_records_why_without_the_cookie(self):
        credential = store(TRANSLATION)
        credential.mark_expired('session rejected (401)')

        credential.refresh_from_db()
        self.assertEqual(credential.status, EagleCredential.EXPIRED)
        self.assertIn('401', credential.last_failure_detail)
        self.assertNotIn('SUPERSECRET', credential.last_failure_detail)
        self.assertFalse(credential.is_usable)


# ===================================================================== #
# Refusing to run unconfigured
# ===================================================================== #

class UnconfiguredTestCase(NoNetworkMixin, TestCase):

    def test_no_credential_means_a_clean_refusal(self):
        service = EagleService()
        self.assertFalse(service.is_configured())
        with self.assertRaises(EagleNotConfigured):
            service.list_available_tasks(TRANSLATION)

    def test_an_expired_credential_is_not_usable(self):
        store(TRANSLATION, status=EagleCredential.EXPIRED)
        with self.assertRaises(EagleNotConfigured):
            EagleService().list_available_tasks(TRANSLATION)

    def test_an_unknown_product_is_refused(self):
        with self.assertRaises(Exception):
            EagleService().list_available_tasks('linkedin')


# ===================================================================== #
# Detecting a lapsed session by SHAPE
# ===================================================================== #

class SessionExpiryDetectionTestCase(NoNetworkMixin, TestCase):
    """
    The failure signatures differ between the two products and neither is a
    401 on the endpoint we would naturally check first.
    """

    def test_translation_logged_out_is_a_200_with_a_null_user(self):
        client = TranslationClient(COOKIE)
        with mock.patch.object(client, '_json', return_value={'user': None}):
            with self.assertRaises(EagleSessionExpired):
                client.whoami()

    def test_translation_signed_in_reports_the_minutes_balance(self):
        client = TranslationClient(COOKIE)
        payload = {'user': {'id': 3739, 'name': 'Shurz', 'role': 'user',
                            'minutes_balance': 15, 'wallet_balance': 0}}
        with mock.patch.object(client, '_json', return_value=payload):
            account = client.whoami()

        self.assertEqual(account.label, 'Shurz')
        self.assertEqual(account.minutes_balance, 15)

    def test_transcription_logged_out_is_a_redirect_not_a_401(self):
        client = TranscriptionClient(COOKIE)
        with mock.patch('core.services.eagle_client._request',
                        return_value=(200, 'https://eaglewebcommerce.com/', {}, b'<html>')):
            with self.assertRaises(EagleSessionExpired):
                client.whoami()

    def test_transcription_task_list_returning_html_is_treated_as_expired(self):
        # Cloudflare challenges and login pages both arrive as HTML with a
        # 200. Parsing failure here means the session, not a bug.
        client = TranscriptionClient(COOKIE)
        with mock.patch('core.services.eagle_client._request',
                        return_value=(200, 'https://transcribe.eaglewebcommerce.com/user/stt/tasks',
                                      {}, b'<!DOCTYPE html><html>login</html>')):
            with self.assertRaises(EagleSessionExpired):
                client.list_available()

    def test_an_expired_session_is_recorded_on_the_credential(self):
        credential = store(TRANSLATION)
        service = EagleService()

        with mock.patch.object(TranslationClient, 'whoami',
                               side_effect=EagleSessionExpired('user: null', 200)):
            with self.assertRaises(EagleAuthError):
                service.check_session(TRANSLATION)

        credential.refresh_from_db()
        self.assertEqual(credential.status, EagleCredential.EXPIRED)

    def test_a_healthy_check_records_the_balance_for_the_operator(self):
        credential = store(TRANSLATION)
        service = EagleService()

        from core.services.eagle_client import EagleAccount
        with mock.patch.object(TranslationClient, 'whoami',
                               return_value=EagleAccount(label='Shurz', minutes_balance=15)):
            result = service.check_session(TRANSLATION)

        credential.refresh_from_db()
        self.assertEqual(credential.status, EagleCredential.ACTIVE)
        self.assertEqual(credential.minutes_balance, 15)
        self.assertEqual(result['minutes_balance'], 15)


# ===================================================================== #
# Media, and the credential boundary
# ===================================================================== #

class MediaBoundaryTestCase(NoNetworkMixin, TestCase):

    def setUp(self):
        store(TRANSCRIPTION)
        store(TRANSLATION)
        self.service = EagleService()

    def _patch_list(self, task):
        return mock.patch.object(
            TranscriptionClient if task.product == TRANSCRIPTION else TranslationClient,
            'list_available', return_value=[task],
        )

    def test_transcription_media_is_public_and_carries_no_headers(self):
        task = RawTask(external_id='37329', product=TRANSCRIPTION, status='IN_PROGRESS',
                       media_url='https://transcribe.eaglewebcommerce.com/transcribe/audio/x.mp3',
                       media_needs_session=False)
        with self._patch_list(task):
            media = self.service.get_task_media('37329', TRANSCRIPTION)

        self.assertTrue(media.url.endswith('.mp3'))
        self.assertEqual(media.headers, {})

    def test_translation_media_does_not_leak_the_cookie_through_the_plain_door(self):
        """
        `get_task_media` must never hand back credentials, even for a
        product that needs them -- otherwise a caller picks up headers it
        then has to remember not to serialize.
        """
        task = RawTask(external_id='4333', product=TRANSLATION, status='available',
                       media_url='https://translation.eaglewebcommerce.com/api/user/tasks/4333/source',
                       media_needs_session=True)
        with self._patch_list(task):
            with mock.patch.object(TranslationClient, 'get_task', return_value=task):
                media = self.service.get_task_media('4333', TRANSLATION)

        self.assertEqual(media.headers, {})
        self.assertNotIn('SUPERSECRET', str(media.headers))

    def test_the_authenticated_door_does_supply_the_cookie(self):
        task = RawTask(external_id='4333', product=TRANSLATION, status='available',
                       media_url='https://translation.eaglewebcommerce.com/api/user/tasks/4333/source',
                       media_needs_session=True)
        with mock.patch.object(TranslationClient, 'get_task', return_value=task):
            media = self.service.get_authenticated_media('4333', TRANSLATION)

        self.assertIn('Cookie', media.headers)
        self.assertIn('SUPERSECRET', media.headers['Cookie'])


# ===================================================================== #
# Normalisation
# ===================================================================== #

class NormalisationTestCase(NoNetworkMixin, TestCase):

    def test_eagle_status_names_map_to_ours_and_unknown_ones_stay_blank(self):
        from .eagle_impl import EXTERNAL_STATUS_MAP

        self.assertEqual(EXTERNAL_STATUS_MAP['available'], 'available')
        self.assertEqual(EXTERNAL_STATUS_MAP['completed'], 'approved')
        self.assertEqual(EXTERNAL_STATUS_MAP['rejected'], 'rejected')
        # Transcription displays "Pending" but stores IN_PROGRESS.
        self.assertEqual(EXTERNAL_STATUS_MAP['IN_PROGRESS'], 'available')
        # An unmapped value must not be guessed into something that drives
        # behaviour.
        self.assertEqual(EXTERNAL_STATUS_MAP.get('some_new_state', ''), '')

    def test_a_translation_row_becomes_a_translation_task(self):
        task = EagleService._to_task(RawTask(
            external_id='4333', product=TRANSLATION, status='available',
            title='C 226.mp3', duration_seconds=618.0, minutes_required=10,
            payout=3.0, media_needs_session=True,
        ))

        self.assertEqual(task.kind, 'translation')
        self.assertEqual(task.duration_seconds, 618)
        self.assertEqual(task.raw['minutes_required'], 10)
        self.assertTrue(task.raw['media_needs_session'])

    def test_a_transcription_row_becomes_a_transcription_task(self):
        task = EagleService._to_task(RawTask(
            external_id='37329', product=TRANSCRIPTION, status='IN_PROGRESS',
            language='English (USA)', duration_seconds=1539.658,
            media_needs_session=False,
        ))

        self.assertEqual(task.kind, 'transcription')
        self.assertEqual(task.duration_seconds, 1539)
        self.assertFalse(task.raw['media_needs_session'])

    def test_available_listing_excludes_tasks_someone_else_holds(self):
        store(TRANSCRIPTION)
        rows = [
            RawTask(external_id='1', product=TRANSCRIPTION, claimed=False),
            RawTask(external_id='2', product=TRANSCRIPTION, claimed=True),
        ]
        with mock.patch.object(TranscriptionClient, 'list_available', return_value=rows):
            available = EagleService().list_available_tasks(TRANSCRIPTION)

        self.assertEqual([t.external_id for t in available], ['1'])


class SessionRotationTestCase(NoNetworkMixin, TestCase):
    """
    THE SESSION COOKIE IS THE CREDENTIAL, AND EAGLE KEEPS RENAMING IT.

    Both products reissue a cookie on essentially every request. The client
    used to discard all of them -- it never read Set-Cookie back -- and so
    replayed the value it was given at setup forever, presenting a session
    the server had already moved past. It works for a while and then stops,
    with nothing in between to explain it: "session expired" on a session
    nobody left.

    Two things made it invisible. `dict(response.headers)` keeps only the
    LAST Set-Cookie when a response rotates several, and every call site
    discarded the headers anyway.
    """

    def test_a_reissued_cookie_replaces_the_old_value(self):
        merged = merge_set_cookies(
            'a=1; translatepro_session=OLD; cf_clearance=xyz',
            ['translatepro_session=NEW; Path=/; HttpOnly; SameSite=Lax'],
        )
        self.assertIn('translatepro_session=NEW', merged)
        self.assertNotIn('OLD', merged)
        # Everything else survives untouched.
        self.assertIn('a=1', merged)
        self.assertIn('cf_clearance=xyz', merged)

    def test_cookie_attributes_never_leak_into_the_request_header(self):
        """Path/HttpOnly/SameSite belong in a Set-Cookie, not a Cookie. A
        stray attribute makes the header malformed."""
        merged = merge_set_cookies('s=1', ['s=2; Path=/; HttpOnly'])
        for attribute in ('Path', 'HttpOnly', 'SameSite', 'Expires'):
            self.assertNotIn(attribute, merged)

    def test_several_rotations_in_one_response_all_apply(self):
        """The reason the header list matters: a dict would keep only the
        last of these."""
        merged = merge_set_cookies(
            'one=a; two=b',
            ['one=A; Path=/', 'two=B; Path=/'],
        )
        self.assertIn('one=A', merged)
        self.assertIn('two=B', merged)

    def test_a_deletion_does_not_blank_the_session(self):
        """`name=; Expires=<past>` is a delete. Honouring it literally would
        hand the next request an empty session and log us out."""
        merged = merge_set_cookies(
            'translatepro_session=LIVE',
            ['translatepro_session=; Expires=Thu, 01 Jan 1970 00:00:00 GMT'],
        )
        self.assertIn('translatepro_session=LIVE', merged)

    def test_nothing_reissued_means_nothing_changes(self):
        original = 'a=1; b=2'
        self.assertEqual(merge_set_cookies(original, []), original)
        self.assertEqual(merge_set_cookies(original, ['a=1']), original)

    def test_rotating_keeps_the_health_record(self):
        """
        A rotation is the SAME session under a new name, so it must not
        reset health the way storing a new credential does. Resetting on
        every call would flap the status all day and make the dashboard
        worthless.
        """
        credential = EagleCredential(product=TRANSLATION)
        credential.set_cookie('session=first')
        # mark_verified saves with update_fields, which needs a real row.
        credential.save()
        credential.mark_verified(account_label='Shurz', minutes_balance=15)

        changed = credential.rotate_cookie('session=second')

        self.assertTrue(changed)
        self.assertEqual(credential.get_cookie_header(), 'session=second')
        self.assertEqual(credential.status, EagleCredential.ACTIVE)
        self.assertEqual(credential.minutes_balance, 15)

    def test_rotating_to_the_same_value_is_not_a_write(self):
        credential = EagleCredential(product=TRANSLATION)
        credential.set_cookie('session=same')
        credential.save()
        self.assertFalse(credential.rotate_cookie('session=same'))
        self.assertFalse(credential.rotate_cookie(''))


class LaravelCsrfTestCase(NoNetworkMixin, TestCase):
    """
    WRITES NEED THE TOKEN ECHOED BACK. Reads do not.

    Laravel sets an `XSRF-TOKEN` cookie and expects any state-changing
    request to return that value in an `X-XSRF-TOKEN` header. A cookie
    travels by itself, which is exactly what CSRF exploits; the header is
    the part that proves the caller could read it.

    THIS WENT UNNOTICED UNTIL THE FIRST LIVE CLAIM. Polling, listing and
    downloading are all GETs, so the entire system looked healthy for weeks.
    The first write -- claiming a real task, with real minutes at stake --
    came back `419 CSRF token mismatch`. Submitting would have failed
    identically a minute later.
    """

    COOKIE = ('XSRF-TOKEN=eyJpdiI6ImFiYyJ9%3D%3D; '
              'translatepro_session=abc123; cf_clearance=xyz')

    def test_the_token_is_url_decoded_for_the_header(self):
        """Laravel writes `...%3D%3D` into the cookie and compares against
        the DECODED value. Echoing the raw cookie fails exactly as if no
        header had been sent at all."""
        header = xsrf_header(self.COOKIE)

        self.assertEqual(header['X-XSRF-TOKEN'], 'eyJpdiI6ImFiYyJ9==')
        self.assertNotIn('%3D', header['X-XSRF-TOKEN'])

    def test_no_token_in_the_cookie_means_no_header(self):
        """Better to send nothing than an empty header: the platform's own
        error then names the real problem."""
        self.assertEqual(xsrf_header('session=abc; other=1'), {})
        self.assertEqual(xsrf_header(''), {})

    def test_a_write_carries_the_header(self):
        client = TranslationClient(self.COOKIE)
        seen = {}

        def fake(url, cookie, method='GET', body=None, headers=None, **kw):
            seen['method'] = method
            seen['headers'] = headers or {}
            return 200, url, [], b'{}'

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            client.claim('4452')

        self.assertEqual(seen['method'], 'POST')
        self.assertIn('X-XSRF-TOKEN', seen['headers'])

    def test_a_read_does_not_need_it(self):
        """Harmless either way, but sending it on a GET would suggest the
        header is what authenticates, and it is not."""
        client = TranslationClient(self.COOKIE)
        seen = {}

        def fake(url, cookie, method='GET', body=None, headers=None, **kw):
            seen['headers'] = headers or {}
            return 200, url, [], b'{"user": {"name": "x"}}'

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            client.whoami()

        self.assertNotIn('X-XSRF-TOKEN', seen['headers'])


# ===================================================================== #
# The login credential at rest
# ===================================================================== #

class LoginCredentialStorageTestCase(NoNetworkMixin, TestCase):

    def test_the_password_is_not_stored_in_clear_text(self):
        credential = store_login(TRANSLATION)

        blob = bytes(EagleCredential.objects.get(pk=credential.pk).password_encrypted)

        self.assertNotIn(FAKE_PASSWORD.encode(), blob)

    def test_it_round_trips(self):
        store_login(TRANSLATION)
        self.assertEqual(
            EagleCredential.objects.get(product=TRANSLATION).get_login(),
            (FAKE_USERNAME, FAKE_PASSWORD),
        )

    def test_str_carries_no_login_material(self):
        rendered = str(store_login(TRANSLATION))
        self.assertNotIn(FAKE_PASSWORD, rendered)
        self.assertNotIn(FAKE_USERNAME, rendered)

    def test_an_empty_username_or_password_is_refused(self):
        credential = EagleCredential(product=TRANSLATION)
        with self.assertRaises(ValueError):
            credential.set_login('', FAKE_PASSWORD)
        with self.assertRaises(ValueError):
            credential.set_login(FAKE_USERNAME, '')

    def test_an_undecryptable_login_blob_reads_as_absent_rather_than_exploding(self):
        credential = store_login(TRANSLATION)
        credential.password_encrypted = b'not-a-fernet-token'
        credential.save(update_fields=['password_encrypted'])

        self.assertEqual(
            EagleCredential.objects.get(pk=credential.pk).get_login(), (None, None))

    def test_has_login_is_false_until_both_fields_are_set(self):
        credential = EagleCredential(product=TRANSLATION)
        self.assertFalse(credential.has_login)
        credential.set_login(FAKE_USERNAME, FAKE_PASSWORD)
        self.assertTrue(credential.has_login)

    def test_a_credential_with_only_a_cookie_has_no_login(self):
        credential = store(TRANSLATION)
        self.assertFalse(credential.has_login)
        self.assertEqual(credential.get_login(), (None, None))

    def test_clearing_login_leaves_the_cookie_untouched(self):
        credential = store_with_login(TRANSCRIPTION)

        credential.clear_login()

        self.assertFalse(credential.has_login)
        self.assertEqual(credential.get_login(), (None, None))
        self.assertEqual(credential.get_cookie_header(), COOKIE)

    def test_clearing_login_also_clears_the_cooldown(self):
        credential = store_with_login(TRANSLATION)
        credential.record_login_failure('bad password')
        self.assertTrue(credential.login_on_cooldown())

        credential.clear_login()

        self.assertFalse(credential.login_on_cooldown())


# ===================================================================== #
# Transcription login -- POST /login, form-encoded
# ===================================================================== #

class TranscriptionLoginFlowTestCase(NoNetworkMixin, TestCase):
    """
    Every field name and the success/failure signal here were read off the
    live form and Laravel's own well-documented AuthenticatesUsers
    behaviour -- see core/services/eagle_client.py's login() docstring.
    """

    def _fake(self, post_status=200, post_final_url=None, post_body=b'',
              get_body=b'<input type="hidden" name="_token" value="tok-123">'):
        calls = []

        def fake(url, cookie, method='GET', body=None, headers=None, **kw):
            calls.append({'url': url, 'method': method, 'body': body, 'headers': headers})
            if method == 'GET':
                return 200, url, [], get_body
            return (post_status,
                    post_final_url or 'https://transcribe.eaglewebcommerce.com/login',
                    ['eagletranscribe_session=authed'], post_body)
        return fake, calls

    def test_a_successful_login_adopts_the_new_cookie(self):
        client = TranscriptionClient('')
        fake, _ = self._fake(
            post_final_url='https://transcribe.eaglewebcommerce.com/user/stt/tasks')

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            client.login(FAKE_USERNAME, FAKE_PASSWORD)

        self.assertIn('eagletranscribe_session=authed', client.cookie)

    def test_a_rejected_login_stays_on_login_and_raises(self):
        client = TranscriptionClient('')
        fake, _ = self._fake(
            post_final_url='https://transcribe.eaglewebcommerce.com/login',
            post_body=b'<div class="invalid-feedback">'
                      b'These credentials do not match our records.</div>')

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, 'wrong-password')

        self.assertIn('do not match', str(ctx.exception))

    def test_the_password_never_appears_in_the_rejection_message(self):
        client = TranscriptionClient('')
        fake, _ = self._fake(post_final_url='https://transcribe.eaglewebcommerce.com/login')

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)

        self.assertNotIn(FAKE_PASSWORD, str(ctx.exception))

    def test_remember_is_never_sent(self):
        client = TranscriptionClient('')
        fake, calls = self._fake(
            post_final_url='https://transcribe.eaglewebcommerce.com/user/stt/tasks')

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            client.login(FAKE_USERNAME, FAKE_PASSWORD)

        post_body = calls[1]['body'].decode()
        self.assertNotIn('remember', post_body)

    def test_the_csrf_token_from_the_get_is_carried_into_the_post(self):
        client = TranscriptionClient('')
        fake, calls = self._fake(
            post_final_url='https://transcribe.eaglewebcommerce.com/user/stt/tasks',
            get_body=b'<input type="hidden" name="_token" value="the-real-token">')

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            client.login(FAKE_USERNAME, FAKE_PASSWORD)

        post_body = calls[1]['body'].decode()
        self.assertIn('_token=the-real-token', post_body)

    def test_no_csrf_token_on_the_login_page_is_a_clean_failure_not_a_crash(self):
        client = TranscriptionClient('')
        fake, _ = self._fake(get_body=b'<html>no token here</html>')

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            with self.assertRaises(EagleLoginRejected):
                client.login(FAKE_USERNAME, FAKE_PASSWORD)

    def test_recaptcha_is_never_invented_or_sent(self):
        """
        The hidden recaptcha field on the live form has no widget wired to
        it anywhere in the page or in custom.js -- see the login()
        docstring. This asserts the code holds to that: no such field name
        is ever part of the POST body.
        """
        client = TranscriptionClient('')
        fake, calls = self._fake(
            post_final_url='https://transcribe.eaglewebcommerce.com/user/stt/tasks')

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            client.login(FAKE_USERNAME, FAKE_PASSWORD)

        post_body = calls[1]['body'].decode()
        self.assertNotIn('recaptcha', post_body)


# ===================================================================== #
# Translation login -- POST /api/auth/login, JSON
# ===================================================================== #

class TranslationLoginFlowTestCase(NoNetworkMixin, TestCase):
    """
    The endpoint, encoding, header and success shape here were read
    directly out of the SPA's own shipped bundle, not guessed -- see
    login()'s docstring.
    """

    def _fake(self, login_status=200, login_body=b'{"user": null}',
              get_cookies=('XSRF-TOKEN=abc123',)):
        # Discriminate by METHOD, not URL suffix -- "/login" (the GET) and
        # "/api/auth/login" (the POST) both end with "/login", so a suffix
        # match routes both calls through the same branch.
        calls = []

        def fake(url, cookie, method='GET', body=None, headers=None, **kw):
            calls.append({'url': url, 'method': method, 'body': body, 'headers': headers})
            if method == 'GET':
                return 200, url, list(get_cookies), b''
            return login_status, url, ['translatepro_session=authed'], login_body
        return fake, calls

    def test_a_successful_login_adopts_the_new_cookie(self):
        client = TranslationClient('')
        fake, _ = self._fake(login_body=b'{"user": {"id": 1, "name": "Shurz"}}')

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            client.login(FAKE_USERNAME, FAKE_PASSWORD)

        self.assertIn('translatepro_session=authed', client.cookie)

    def test_a_null_user_is_a_failure_even_with_200(self):
        client = TranslationClient('')
        fake, _ = self._fake(login_status=200, login_body=b'{"user": null}')

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            with self.assertRaises(EagleLoginRejected):
                client.login(FAKE_USERNAME, 'wrong-password')

    def test_a_4xx_with_a_message_surfaces_that_message(self):
        client = TranslationClient('')
        fake, _ = self._fake(login_status=422,
                             login_body=b'{"message": "These credentials are invalid."}')

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, 'wrong-password')

        self.assertIn('These credentials are invalid.', str(ctx.exception))

    def test_the_password_never_appears_in_the_rejection_message(self):
        client = TranslationClient('')
        fake, _ = self._fake(login_status=422, login_body=b'{"message": "no"}')

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)

        self.assertNotIn(FAKE_PASSWORD, str(ctx.exception))

    def test_the_xsrf_header_is_derived_from_the_gets_cookie_and_sent_on_the_post(self):
        client = TranslationClient('')
        fake, calls = self._fake(
            login_body=b'{"user": {"name": "Shurz"}}',
            get_cookies=('XSRF-TOKEN=the-real-token',))

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            client.login(FAKE_USERNAME, FAKE_PASSWORD)

        login_call = calls[1]
        self.assertEqual(login_call['headers']['X-XSRF-TOKEN'], 'the-real-token')

    def test_the_body_is_json_with_email_and_password_only(self):
        import json
        client = TranslationClient('')
        fake, calls = self._fake(login_body=b'{"user": {"name": "Shurz"}}')

        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            client.login(FAKE_USERNAME, FAKE_PASSWORD)

        payload = json.loads(calls[1]['body'])
        self.assertEqual(payload, {'email': FAKE_USERNAME, 'password': FAKE_PASSWORD})


# ===================================================================== #
# Automatic re-login: retry-once, scoped to reads only
# ===================================================================== #

class ReloginRetryTestCase(NoNetworkMixin, TestCase):
    """
    The state machine in EagleService._guard. See its own docstring for the
    exact reasoning; these tests are the contract it must hold to.
    """

    def _fake_login(self, cookie='translatepro_session=freshly-logged-in'):
        def fake_login(client_self, username, password):
            client_self.cookie = cookie
        return fake_login

    def test_a_read_recovers_via_one_relogin_and_returns_normally(self):
        credential = store_with_login(TRANSLATION)
        service = EagleService()

        with mock.patch.object(
            TranslationClient, 'whoami',
            side_effect=[EagleSessionExpired('lapsed', 200),
                        EagleAccount(label='Shurz', minutes_balance=42)],
        ), mock.patch.object(TranslationClient, 'login', self._fake_login()):
            result = service.check_session(TRANSLATION)

        self.assertEqual(result['account'], 'Shurz')
        self.assertEqual(result['minutes_balance'], 42)
        credential.refresh_from_db()
        self.assertEqual(credential.status, EagleCredential.ACTIVE)
        self.assertIn('freshly-logged-in', credential.get_cookie_header())

    def test_exactly_one_login_attempt_is_made(self):
        # A plain function assigned directly (not via mock's side_effect=)
        # so instance.login(...) still goes through normal method binding
        # and receives `self` -- a MagicMock's side_effect does not.
        store_with_login(TRANSLATION)
        service = EagleService()
        calls = []

        def counting_login(client_self, username, password):
            calls.append((username, password))
            client_self.cookie = 'translatepro_session=freshly-logged-in'

        with mock.patch.object(
            TranslationClient, 'whoami',
            side_effect=[EagleSessionExpired('lapsed', 200),
                        EagleAccount(label='Shurz')],
        ), mock.patch.object(TranslationClient, 'login', counting_login):
            service.check_session(TRANSLATION)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], (FAKE_USERNAME, FAKE_PASSWORD))

    def test_a_rejected_relogin_raises_login_failed_and_does_not_loop(self):
        credential = store_with_login(TRANSLATION)
        service = EagleService()

        def rejecting_login(client_self, username, password):
            raise EagleLoginRejected('bad credentials', 422)

        with mock.patch.object(
            TranslationClient, 'whoami',
            side_effect=EagleSessionExpired('lapsed', 200),
        ) as whoami_mock, mock.patch.object(TranslationClient, 'login', rejecting_login):
            with self.assertRaises(EagleLoginFailed):
                service.check_session(TRANSLATION)

        # One attempt: the initial call. login() raising means the retry
        # never runs operation_factory a second time.
        self.assertEqual(whoami_mock.call_count, 1)
        credential.refresh_from_db()
        self.assertEqual(credential.status, EagleCredential.EXPIRED)
        self.assertIsNotNone(credential.last_login_failure_at)

    def test_a_second_expiry_after_successful_relogin_raises_and_does_not_loop_again(self):
        credential = store_with_login(TRANSLATION)
        service = EagleService()

        with mock.patch.object(
            TranslationClient, 'whoami',
            side_effect=EagleSessionExpired('still lapsed', 200),
        ) as whoami_mock, mock.patch.object(TranslationClient, 'login', self._fake_login()):
            with self.assertRaises(EagleAuthError):
                service.check_session(TRANSLATION)

        # Initial attempt + exactly one retry after the (successful) relogin
        # -- never a third.
        self.assertEqual(whoami_mock.call_count, 2)
        credential.refresh_from_db()
        self.assertEqual(credential.status, EagleCredential.EXPIRED)

    def test_no_stored_login_keeps_the_original_unconditional_expiry_behaviour(self):
        """Regression guard: a credential with only a cookie (today's only
        shape, still fully supported) must behave byte-for-byte as before
        this feature existed -- no login attempt, ever."""
        credential = store(TRANSLATION)
        service = EagleService()

        with mock.patch.object(
            TranslationClient, 'whoami', side_effect=EagleSessionExpired('lapsed', 200),
        ) as whoami_mock, mock.patch.object(TranslationClient, 'login') as login_mock:
            with self.assertRaises(EagleAuthError):
                service.check_session(TRANSLATION)
            login_mock.assert_not_called()

        self.assertEqual(whoami_mock.call_count, 1)
        credential.refresh_from_db()
        self.assertEqual(credential.status, EagleCredential.EXPIRED)

    def test_claim_never_auto_retries_even_with_login_stored(self):
        store_with_login(TRANSLATION)
        service = EagleService()

        with mock.patch.object(
            TranslationClient, 'claim', side_effect=EagleSessionExpired('lapsed', 200),
        ) as claim_mock, mock.patch.object(TranslationClient, 'login') as login_mock:
            with self.assertRaises(EagleAuthError):
                service.claim_task('4452', TRANSLATION)
            login_mock.assert_not_called()

        self.assertEqual(claim_mock.call_count, 1)

    def test_submit_never_auto_retries_even_with_login_stored(self):
        store_with_login(TRANSLATION)
        service = EagleService()

        with mock.patch.object(
            TranslationClient, 'submit', side_effect=EagleSessionExpired('lapsed', 200),
        ) as submit_mock, mock.patch.object(TranslationClient, 'login') as login_mock:
            with self.assertRaises(EagleAuthError):
                service.submit_task('4452', 'content', TRANSLATION)
            login_mock.assert_not_called()

        self.assertEqual(submit_mock.call_count, 1)


# ===================================================================== #
# Login-failure cooldown
# ===================================================================== #

class LoginCooldownTestCase(NoNetworkMixin, TestCase):

    def test_a_recent_login_failure_suppresses_the_next_automatic_attempt(self):
        credential = store_with_login(TRANSLATION)
        credential.record_login_failure('bad password')
        service = EagleService()

        with mock.patch.object(
            TranslationClient, 'whoami', side_effect=EagleSessionExpired('lapsed', 200),
        ) as whoami_mock, mock.patch.object(TranslationClient, 'login') as login_mock:
            with self.assertRaises(EagleAuthError):
                service.check_session(TRANSLATION)
            login_mock.assert_not_called()

        self.assertEqual(whoami_mock.call_count, 1)

    def test_a_failure_outside_the_cooldown_window_allows_a_retry(self):
        credential = store_with_login(TRANSLATION)
        credential.record_login_failure('bad password')
        credential.last_login_failure_at = (
            timezone.now() - timedelta(seconds=EAGLE_LOGIN_COOLDOWN_SECONDS + 5))
        credential.save(update_fields=['last_login_failure_at'])
        service = EagleService()

        def fake_login(client_self, username, password):
            client_self.cookie = 'translatepro_session=x'

        with mock.patch.object(
            TranslationClient, 'whoami',
            side_effect=[EagleSessionExpired('lapsed', 200), EagleAccount(label='Shurz')],
        ), mock.patch.object(TranslationClient, 'login', fake_login):
            result = service.check_session(TRANSLATION)

        self.assertEqual(result['account'], 'Shurz')

    def test_manual_login_verification_bypasses_the_cooldown(self):
        """--set-login's own verification (EagleService.login) is a
        deliberate new attempt by a human, not the automatic retry loop the
        cooldown exists to bound -- it must never be blocked by it."""
        credential = store_with_login(TRANSLATION)
        credential.record_login_failure('bad password')
        self.assertTrue(credential.login_on_cooldown())
        service = EagleService()

        def fake_login(client_self, username, password):
            client_self.cookie = 'translatepro_session=x'

        with mock.patch.object(TranslationClient, 'login', fake_login), \
             mock.patch.object(TranslationClient, 'whoami',
                               return_value=EagleAccount(label='Shurz', minutes_balance=10)):
            result = service.login(TRANSLATION)

        self.assertEqual(result['account'], 'Shurz')


# ===================================================================== #
# `eagle_credential` CLI: --set-login / --clear-login / --show
# ===================================================================== #

class EagleCredentialLoginCommandTestCase(NoNetworkMixin, TestCase):

    def test_set_login_stores_encrypted_credentials_and_verifies(self):
        def fake_login(client_self, username, password):
            client_self.cookie = 'translatepro_session=verified'

        out = io.StringIO()
        with mock.patch('builtins.input', return_value=FAKE_USERNAME), \
             mock.patch('getpass.getpass', return_value=FAKE_PASSWORD), \
             mock.patch.object(TranslationClient, 'login', fake_login), \
             mock.patch.object(TranslationClient, 'whoami',
                               return_value=EagleAccount(label='Shurz', minutes_balance=5)):
            call_command('eagle_credential', '--set-login', TRANSLATION, stdout=out)

        credential = EagleCredential.objects.get(product=TRANSLATION)
        self.assertTrue(credential.has_login)
        self.assertEqual(credential.get_login(), (FAKE_USERNAME, FAKE_PASSWORD))
        rendered = out.getvalue()
        self.assertIn('logged in as Shurz', rendered)
        self.assertNotIn(FAKE_PASSWORD, rendered)

    def test_set_login_reports_rejection_without_leaking_the_password(self):
        def rejecting_login(client_self, username, password):
            raise EagleLoginRejected('These credentials do not match our records.', 422)

        out = io.StringIO()
        with mock.patch('builtins.input', return_value=FAKE_USERNAME), \
             mock.patch('getpass.getpass', return_value=FAKE_PASSWORD), \
             mock.patch.object(TranslationClient, 'login', rejecting_login):
            call_command('eagle_credential', '--set-login', TRANSLATION, stdout=out)

        rendered = out.getvalue()
        self.assertIn('rejected', rendered)
        self.assertNotIn(FAKE_PASSWORD, rendered)
        # Verification failing is not the same as refusing to store it --
        # the operator typed a real value and should not have to retype it
        # to try --set-login again after fixing whatever was wrong.
        self.assertTrue(EagleCredential.objects.get(product=TRANSLATION).has_login)

    def test_an_empty_username_aborts_before_storing_anything(self):
        with mock.patch('builtins.input', return_value=''):
            with self.assertRaises(CommandError):
                call_command('eagle_credential', '--set-login', TRANSCRIPTION)

        self.assertFalse(EagleCredential.objects.filter(product=TRANSCRIPTION).exists())

    def test_an_empty_password_aborts_before_storing_anything(self):
        with mock.patch('builtins.input', return_value=FAKE_USERNAME), \
             mock.patch('getpass.getpass', return_value=''):
            with self.assertRaises(CommandError):
                call_command('eagle_credential', '--set-login', TRANSCRIPTION)

        self.assertFalse(EagleCredential.objects.filter(product=TRANSCRIPTION).exists())

    def test_clear_login_removes_only_the_login_not_the_cookie(self):
        store_with_login(TRANSCRIPTION)
        out = io.StringIO()

        call_command('eagle_credential', '--clear-login', TRANSCRIPTION, stdout=out)

        credential = EagleCredential.objects.get(product=TRANSCRIPTION)
        self.assertFalse(credential.has_login)
        self.assertEqual(credential.get_cookie_header(), COOKIE)

    def test_clear_login_on_a_credential_with_no_login_is_a_clean_no_op(self):
        store(TRANSCRIPTION)
        out = io.StringIO()

        call_command('eagle_credential', '--clear-login', TRANSCRIPTION, stdout=out)

        self.assertIn('No login credentials stored', out.getvalue())
        self.assertEqual(EagleCredential.objects.get(product=TRANSCRIPTION).get_cookie_header(), COOKIE)

    def test_show_reports_login_configured_without_ever_printing_it(self):
        store_with_login(TRANSCRIPTION)
        out = io.StringIO()

        call_command('eagle_credential', '--show', stdout=out)

        rendered = out.getvalue()
        self.assertIn('login=configured', rendered)
        self.assertNotIn(FAKE_PASSWORD, rendered)
        self.assertNotIn(FAKE_USERNAME, rendered)

    def test_show_reports_not_configured_for_a_cookie_only_credential(self):
        store(TRANSCRIPTION)
        out = io.StringIO()

        call_command('eagle_credential', '--show', stdout=out)

        self.assertIn('login=not-configured', out.getvalue())


# ===================================================================== #
# Cross-cutting: the password never surfaces anywhere
# ===================================================================== #

class LoginSecretNeverLeaksTestCase(NoNetworkMixin, TestCase):
    """
    The same discipline this file already holds the cookie to, applied to
    the password across every path that touches it.
    """

    def test_never_appears_in_a_client_side_rejection_across_either_product(self):
        for client, login_kwargs in (
            (TranscriptionClient(''), {}),
            (TranslationClient(''), {}),
        ):
            def fake(url, cookie, method='GET', body=None, headers=None, **kw):
                if method == 'GET':
                    return (200, url, [],
                           b'<input type="hidden" name="_token" value="t">')
                return 422, url, [], b'{"message": "no"}'

            with mock.patch('core.services.eagle_client._request', side_effect=fake):
                with self.assertRaises(EagleLoginRejected) as ctx:
                    client.login(FAKE_USERNAME, FAKE_PASSWORD)
            self.assertNotIn(FAKE_PASSWORD, str(ctx.exception))

    def test_never_appears_on_the_stored_credentials_failure_detail(self):
        credential = store_with_login(TRANSLATION)
        service = EagleService()

        def rejecting_login(client_self, username, password):
            raise EagleLoginRejected('rejected', 422)

        with mock.patch.object(
            TranslationClient, 'whoami', side_effect=EagleSessionExpired('lapsed', 200),
        ), mock.patch.object(TranslationClient, 'login', rejecting_login):
            with self.assertRaises(EagleLoginFailed):
                service.check_session(TRANSLATION)

        credential.refresh_from_db()
        self.assertNotIn(FAKE_PASSWORD, credential.last_failure_detail)

    def test_never_appears_in_eagleservice_login_failure_either(self):
        credential = store_login(TRANSLATION)
        service = EagleService()

        def rejecting_login(client_self, username, password):
            raise EagleLoginRejected('rejected', 422)

        with mock.patch.object(TranslationClient, 'login', rejecting_login):
            with self.assertRaises(EagleLoginFailed) as ctx:
                service.login(TRANSLATION)

        self.assertNotIn(FAKE_PASSWORD, str(ctx.exception))
        credential.refresh_from_db()
        self.assertNotIn(FAKE_PASSWORD, credential.last_failure_detail)
