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
import threading
import time
from datetime import timedelta
from unittest import mock

import psycopg
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from core.services.eagle_client import (
    merge_set_cookies,
    xsrf_header,
    EagleAccount, EagleHTTPError, EagleLoginRejected, EagleSessionExpired,
    RawTask, TranscriptionClient, TranslationClient,
    EAGLE_ROOT_HOST, TRANSLATE_HOST,
)

from .credentials import EAGLE_LOGIN_COOLDOWN_SECONDS, TRANSCRIPTION, TRANSLATION, EagleCredential
from .eagle import (
    EagleAuthError, EagleBusy, EagleIntegrationError, EagleLoginFailed,
    EagleNotConfigured,
)
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
        # BOTH low-level transport functions, not just _request. Confirmed
        # the hard way, in this same session: TranscriptionClient.login()'s
        # POST goes through _request_first_hop (added so the login diagnostic
        # can see a redirect's own Set-Cookie/Location without urllib
        # collapsing the chain -- see its docstring), which this mixin did
        # NOT cover until a test run exercising it made five real requests
        # to Eagle's actual login endpoint, each answered with a genuine 419
        # from a bogus test CSRF token. Never again, on either function.
        for target in ('core.services.eagle_client._request',
                       'core.services.eagle_client._request_first_hop'):
            patcher = mock.patch(
                target,
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

    def test_transcription_403_is_also_treated_as_a_lapsed_session(self):
        """
        Observed live, 2026-09-10: a stale transcription session returned a
        flat 403 on this exact endpoint -- never the redirect above -- for
        10+ hours, because EagleHTTPError (this method's old behaviour for
        any >=400) is not one of the categories _guard() retries via
        automatic re-login. A real re-login was then confirmed to fix it
        immediately, which is what justifies routing 403 specifically
        through EagleSessionExpired rather than the generic error.
        """
        client = TranscriptionClient(COOKIE)
        with mock.patch('core.services.eagle_client._request',
                        return_value=(403, 'https://transcribe.eaglewebcommerce.com/user/stt/tasks',
                                      {}, b'')):
            with self.assertRaises(EagleSessionExpired):
                client.whoami()

    def test_transcription_a_different_4xx_stays_a_plain_http_error(self):
        # Scoped deliberately to 403 alone -- nothing here has evidence that
        # any other status shares the stale-session meaning, so anything
        # else should keep failing loudly as a generic error rather than
        # being silently folded into the retry path too.
        client = TranscriptionClient(COOKIE)
        with mock.patch('core.services.eagle_client._request',
                        return_value=(404, 'https://transcribe.eaglewebcommerce.com/user/stt/tasks',
                                      {}, b'')):
            with self.assertRaises(EagleHTTPError) as ctx:
                client.whoami()
        self.assertNotIsInstance(ctx.exception, EagleSessionExpired)

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

    # The dashboard's "Available Minutes" stat card, as it really renders.
    _DASHBOARD = (
        '<div class="card-body">'
        '<button id="withdrawal">Withdraw</button>'
        '<p class="fs-12 font-weight-bold">Available Balance</p>'
        '<h6 class="mb-0"><strong>$1</strong></h6></div>'
        '<div class="card-body">'
        '<p class="fs-12 font-weight-bold">Available Minutes</p>'
        '<h2 class="mb-0"><span class="number-font-chars">{n}</span></h2></div>'
        '<div class="card-body">'
        '<p class="fs-12 font-weight-bold">Total Minutes Awarded</p>'
        '<h2 class="mb-0"><span class="number-font-chars">4210</span></h2></div>'
    )

    def _transcription_pages(self, minutes_html):
        """A _request stand-in: the task panel for the session check, then
        the dashboard for the balance."""
        panel = (200, 'https://transcribe.eaglewebcommerce.com/user/stt/tasks',
                 {}, b'<html><body>tasks</body></html>')
        dash = (200, 'https://transcribe.eaglewebcommerce.com/user/dashboard',
                {}, minutes_html.encode())

        def fake(url, *a, **k):
            return dash if url.endswith('/user/dashboard') else panel
        return fake

    def test_transcription_reads_available_minutes_from_the_dashboard(self):
        client = TranscriptionClient(COOKIE)
        with mock.patch('core.services.eagle_client._request',
                        side_effect=self._transcription_pages(self._DASHBOARD.format(n='9'))):
            account = client.whoami()

        # The "Available Minutes" card -- not "Available Balance" ($1, money)
        # and not "Total Minutes Awarded" (4210, a lifetime counter).
        self.assertEqual(account.minutes_balance, 9)

    def test_transcription_available_minutes_handles_thousands(self):
        client = TranscriptionClient(COOKIE)
        with mock.patch('core.services.eagle_client._request',
                        side_effect=self._transcription_pages(self._DASHBOARD.format(n='1,234'))):
            account = client.whoami()
        self.assertEqual(account.minutes_balance, 1234)

    def test_transcription_balance_is_unknown_not_zero_when_unreadable(self):
        from core.services.eagle_client import _parse_available_minutes

        self.assertIsNone(_parse_available_minutes(''))
        self.assertIsNone(_parse_available_minutes('<p>Available Minutes</p><h2>soon</h2>'))
        # A missing dashboard must not fail the session check whoami already passed.
        client = TranscriptionClient(COOKIE)

        def fake(url, *a, **k):
            if url.endswith('/user/dashboard'):
                return (503, url, {}, b'')
            return (200, 'https://transcribe.eaglewebcommerce.com/user/stt/tasks', {}, b'ok')
        with mock.patch('core.services.eagle_client._request', side_effect=fake):
            account = client.whoami()
        self.assertIsNone(account.minutes_balance)

    def test_transcription_balance_reaches_the_credential(self):
        credential = store(TRANSCRIPTION)
        service = EagleService()

        from core.services.eagle_client import EagleAccount
        with mock.patch.object(TranscriptionClient, 'whoami',
                               return_value=EagleAccount(label='Eagle Transcribe',
                                                         minutes_balance=125)):
            result = service.check_session(TRANSCRIPTION)

        credential.refresh_from_db()
        self.assertEqual(credential.minutes_balance, 125)
        self.assertEqual(result['minutes_balance'], 125)


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

    def test_the_reject_reason_survives_normalisation(self):
        """
        FOUND, AND FIXED, FROM A REAL REJECTED TASK'S RAW RESPONSE
        (2026-09-18): `_to_task` used to build its own narrow `raw` dict
        from a fixed list of keys that never included `reject_reason` --
        so sync.py's own lookup for that exact key always found an empty
        string, no matter what Eagle actually sent. Every rejection reason
        was silently replaced by the generic fallback message.
        """
        task = EagleService._to_task(RawTask(
            external_id='6021', product=TRANSLATION, status='rejected',
            raw={'reject_reason': 'Duplicate name in the speaker list.'},
        ))

        self.assertEqual(task.raw['reject_reason'], 'Duplicate name in the speaker list.')

    def test_a_missing_reject_reason_normalises_to_an_empty_string(self):
        task = EagleService._to_task(RawTask(
            external_id='4333', product=TRANSLATION, status='completed',
        ))

        self.assertEqual(task.raw['reject_reason'], '')

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

    THE GET AND THE POST ARE MOCKED SEPARATELY, AT DIFFERENT SEAMS. The GET
    (fetching the page for a fresh `_token`) still goes through `_req()` /
    `_request`, exactly as every other call in this file does. The POST
    goes through `_request_first_hop` instead -- deliberately NOT through
    `_request` -- so it can hand back a `Location` header the way a real,
    unfollowed 302 would, which `_request`'s `(status, final_url, cookies,
    body)` shape has no room for. Patching only `_request` here (the way
    every other test in this file does) would silently let the POST fall
    through to the real function -- confirmed the hard way: an earlier
    version of this test file did exactly that and made five real requests
    to Eagle's live login endpoint before `NoNetworkMixin` was extended to
    also cover `_request_first_hop`.
    """

    def _fake_get(self, status=200,
                  body=b'<input type="hidden" name="_token" value="tok-123">'):
        calls = []

        def fake(url, cookie, method='GET', body_=None, headers=None, **kw):
            calls.append({'url': url, 'method': method, 'headers': headers})
            return status, url, [], body
        return fake, calls

    def _fake_post(self, status=302, location=None, set_cookies=(), response_body=b''):
        # NOTE: the inner fake's `body` parameter must be named exactly
        # `body` -- login() calls _request_first_hop(..., body=form_body,
        # ...) as a KEYWORD, and a mismatched parameter name here silently
        # swallows it into **kw instead of raising, which is how this test
        # helper originally shipped broken: calls[0]['body'] was always
        # None because the real POST body never reached it.
        calls = []

        def fake(url, cookie, method='POST', body=None, headers=None, **kw):
            calls.append({'url': url, 'method': method, 'body': body, 'headers': headers})
            return status, (location or ''), list(set_cookies), response_body
        return fake, calls

    def test_a_successful_redirect_adopts_the_new_cookie(self):
        """Category 1: 302 -> /user/stt/tasks, with a fresh session cookie
        on the redirect response ITSELF -- the exact shape _request would
        have thrown away."""
        client = TranscriptionClient('')
        get_fake, _ = self._fake_get()
        post_fake, _ = self._fake_post(
            status=302,
            location='https://transcribe.eaglewebcommerce.com/user/stt/tasks',
            set_cookies=['eagletranscribe_session=freshly-authenticated-session'],
        )

        with mock.patch('core.services.eagle_client._request', side_effect=get_fake), \
             mock.patch('core.services.eagle_client._request_first_hop', side_effect=post_fake):
            client.login(FAKE_USERNAME, FAKE_PASSWORD)

        self.assertIn('eagletranscribe_session=freshly-authenticated-session', client.cookie)

    def test_a_rejected_redirect_back_to_login_raises(self):
        """Category 2: 302 -> /login with a flashed validation error."""
        client = TranscriptionClient('')
        get_fake, _ = self._fake_get()
        post_fake, _ = self._fake_post(
            status=302, location='https://transcribe.eaglewebcommerce.com/login',
        )

        with mock.patch('core.services.eagle_client._request', side_effect=get_fake), \
             mock.patch('core.services.eagle_client._request_first_hop', side_effect=post_fake):
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, 'wrong-password')

        self.assertIn('Eagle rejected these credentials', str(ctx.exception))

    def test_a_scraped_validation_error_is_surfaced_when_present(self):
        client = TranscriptionClient('')
        get_fake, _ = self._fake_get()
        post_fake, _ = self._fake_post(
            status=302, location='https://transcribe.eaglewebcommerce.com/login',
            response_body=b'<div class="invalid-feedback">'
                          b'These credentials do not match our records.</div>',
        )

        with mock.patch('core.services.eagle_client._request', side_effect=get_fake), \
             mock.patch('core.services.eagle_client._request_first_hop', side_effect=post_fake):
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, 'wrong-password')

        self.assertIn('do not match', str(ctx.exception))

    def test_a_non_redirect_error_is_rejected_not_treated_as_success(self):
        """Category 3: a plain error status, no redirect at all -- the 419
        (CSRF mismatch) shape Eagle's real server actually returned during
        the incident that motivated this diagnostic."""
        client = TranscriptionClient('')
        get_fake, _ = self._fake_get()
        post_fake, _ = self._fake_post(status=419, location='')

        with mock.patch('core.services.eagle_client._request', side_effect=get_fake), \
             mock.patch('core.services.eagle_client._request_first_hop', side_effect=post_fake):
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)

        self.assertIn('419', str(ctx.exception))

    def test_an_unexpected_200_with_no_redirect_is_never_assumed_successful(self):
        """Laravel's login route always redirects, success or failure. A
        200 straight back is a shape nothing has confirmed the meaning of
        -- raise rather than guess it means success."""
        client = TranscriptionClient('')
        get_fake, _ = self._fake_get()
        post_fake, _ = self._fake_post(status=200, location='')

        with mock.patch('core.services.eagle_client._request', side_effect=get_fake), \
             mock.patch('core.services.eagle_client._request_first_hop', side_effect=post_fake):
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)

        self.assertIn('has not been observed before', str(ctx.exception))

    def test_a_redirect_with_no_location_header_is_a_clean_failure(self):
        client = TranscriptionClient('')
        get_fake, _ = self._fake_get()
        post_fake, _ = self._fake_post(status=302, location='')

        with mock.patch('core.services.eagle_client._request', side_effect=get_fake), \
             mock.patch('core.services.eagle_client._request_first_hop', side_effect=post_fake):
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)

        self.assertIn('no Location header', str(ctx.exception))

    def test_the_password_never_appears_in_the_rejection_message(self):
        client = TranscriptionClient('')
        get_fake, _ = self._fake_get()
        post_fake, _ = self._fake_post(
            status=302, location='https://transcribe.eaglewebcommerce.com/login')

        with mock.patch('core.services.eagle_client._request', side_effect=get_fake), \
             mock.patch('core.services.eagle_client._request_first_hop', side_effect=post_fake):
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)

        self.assertNotIn(FAKE_PASSWORD, str(ctx.exception))

    def test_no_credential_cookie_or_token_value_appears_in_the_diagnostic_log(self):
        """Category 5. The diagnostic log line added for this investigation
        reports booleans/paths, never values -- confirm across both a
        successful and a rejected attempt."""
        client = TranscriptionClient('')
        get_fake, _ = self._fake_get(
            body=b'<input type="hidden" name="_token" value="super-secret-csrf-token">')
        post_fake, _ = self._fake_post(
            status=302,
            location='https://transcribe.eaglewebcommerce.com/user/stt/tasks',
            set_cookies=['eagletranscribe_session=super-secret-session-value'],
        )

        with self.assertLogs('eagle', level='INFO') as captured:
            with mock.patch('core.services.eagle_client._request', side_effect=get_fake), \
                 mock.patch('core.services.eagle_client._request_first_hop', side_effect=post_fake):
                client.login(FAKE_USERNAME, FAKE_PASSWORD)

        rendered = '\n'.join(captured.output)
        for secret in (FAKE_PASSWORD, FAKE_USERNAME, 'super-secret-csrf-token',
                       'super-secret-session-value'):
            self.assertNotIn(secret, rendered)

    def test_remember_is_never_sent(self):
        client = TranscriptionClient('')
        get_fake, _ = self._fake_get()
        post_fake, calls = self._fake_post(
            status=302, location='https://transcribe.eaglewebcommerce.com/user/stt/tasks')

        with mock.patch('core.services.eagle_client._request', side_effect=get_fake), \
             mock.patch('core.services.eagle_client._request_first_hop', side_effect=post_fake):
            client.login(FAKE_USERNAME, FAKE_PASSWORD)

        post_body = calls[0]['body'].decode()
        self.assertNotIn('remember', post_body)

    def test_the_csrf_token_from_the_get_is_carried_into_the_post(self):
        client = TranscriptionClient('')
        get_fake, _ = self._fake_get(
            body=b'<input type="hidden" name="_token" value="the-real-token">')
        post_fake, calls = self._fake_post(
            status=302, location='https://transcribe.eaglewebcommerce.com/user/stt/tasks')

        with mock.patch('core.services.eagle_client._request', side_effect=get_fake), \
             mock.patch('core.services.eagle_client._request_first_hop', side_effect=post_fake):
            client.login(FAKE_USERNAME, FAKE_PASSWORD)

        post_body = calls[0]['body'].decode()
        self.assertIn('_token=the-real-token', post_body)

    def test_no_csrf_token_on_the_login_page_is_a_clean_failure_not_a_crash(self):
        client = TranscriptionClient('')
        get_fake, _ = self._fake_get(body=b'<html>no token here</html>')

        with mock.patch('core.services.eagle_client._request', side_effect=get_fake):
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
        get_fake, _ = self._fake_get()
        post_fake, calls = self._fake_post(
            status=302, location='https://transcribe.eaglewebcommerce.com/user/stt/tasks')

        with mock.patch('core.services.eagle_client._request', side_effect=get_fake), \
             mock.patch('core.services.eagle_client._request_first_hop', side_effect=post_fake):
            client.login(FAKE_USERNAME, FAKE_PASSWORD)

        post_body = calls[0]['body'].decode()
        self.assertNotIn('recaptcha', post_body)


class RedirectCookieRealServerTestCase(TestCase):
    """
    Category 4, against a REAL local HTTP server -- never Eagle -- proving
    _request_first_hop's actual urllib mechanics, not a mock's approximation
    of them. Deliberately does NOT subclass NoNetworkMixin: that mixin
    blocks the exact two functions this test exists to exercise for real.

    THIS IS WHAT THE MOCKED TESTS ABOVE CANNOT PROVE. Every other login test
    in this file replaces `_request`/`_request_first_hop` outright, so none
    of them touch urllib's real redirect handling at all -- confirmed
    against the installed CPython source during this investigation, not
    assumed. This spins up a real (local, loopback-only) server that mimics
    the one behaviour that matters: issuing a fresh Set-Cookie ON THE
    REDIRECT RESPONSE ITSELF, the way Laravel's session()->regenerate()
    typically does after Auth::login(). It proves two things at once: (a)
    _request_first_hop captures that cookie instead of losing it, and (b)
    plain _request (allow_redirects=True, auto-following) demonstrably does
    NOT -- the exact gap this whole diagnostic exists to close.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class _RedirectFixtureHandler(BaseHTTPRequestHandler):
            def log_message(self, *a, **kw):
                pass  # keep test output quiet; nothing here is secret anyway

            def do_POST(self):
                # Absolute, matching what Laravel's redirect()/url() helpers
                # actually emit -- Location is technically fine either way,
                # but this keeps the test unambiguous about what a raw
                # header looks like rather than exercising relative-URL
                # resolution, which isn't what this test is proving.
                self.send_response(302)
                self.send_header('Location', f'http://{self.headers.get("Host")}/destination')
                self.send_header('Set-Cookie', 'session=freshly-issued-on-redirect')
                self.send_header('Content-Length', '0')
                self.end_headers()

            def do_GET(self):
                # The hop a redirect lands on, if something DOES follow it.
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'ok')

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), _RedirectFixtureHandler)
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        import threading
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        super().tearDownClass()

    def test_request_first_hop_captures_the_redirects_own_set_cookie(self):
        from core.services.eagle_client import _request_first_hop

        status, location, set_cookies, _ = _request_first_hop(
            f'{self.base}/login', '', method='POST', body=b'x=1',
        )

        self.assertEqual(status, 302)
        self.assertEqual(location, f'{self.base}/destination')
        self.assertTrue(any('freshly-issued-on-redirect' in c for c in set_cookies))

    def test_plain_request_following_the_redirect_loses_that_same_cookie(self):
        """The gap this diagnostic exists to close, demonstrated directly:
        the SAME server, the SAME cookie, but through _request's default
        auto-following -- the intermediate Set-Cookie never reaches the
        caller at all."""
        from core.services.eagle_client import _request

        status, final_url, set_cookies, body = _request(
            f'{self.base}/login', '', method='POST', body=b'x=1',
        )

        self.assertEqual(status, 200)  # landed on /destination, followed fully
        self.assertEqual(body, b'ok')
        self.assertFalse(any('freshly-issued-on-redirect' in c for c in set_cookies),
                         'the redirect-hop cookie should NOT survive auto-following')


# ===================================================================== #
# Translation login -- POST /api/auth/login, JSON
# ===================================================================== #

class TranslationLoginFlowTestCase(NoNetworkMixin, TestCase):
    """
    The SSO bridge sequence here was read off a REAL browser capture
    (DevTools Network tab, through an actual login) -- not the SPA bundle.
    /user/service/translation is a root-Eagle route the Translation SPA
    never references at all, which is exactly why the earlier bundle-only
    reverse-engineering (the original, now-replaced direct-POST
    implementation) never found it. See TranslationClient.login()'s own
    docstring for the full five-step sequence and the reasoning behind
    each safeguard below.
    """

    #: A stand-in accessToken -- never a real one, only used to prove it
    #: never leaks anywhere it shouldn't.
    FAKE_TOKEN = 'sso-token-fixture-should-never-appear-in-any-log-or-exception'

    def _fake_hops(self, root_get_status=200,
                   root_get_body=b'<input type="hidden" name="_token" value="tok-123">',
                   root_post_status=302, root_post_location=None, root_post_body=b'',
                   bridge_status=302, bridge_location=None,
                   landing_status=200, landing_cookies=('translatepro_session=freshly-authed',),
                   whoami_status=200, whoami_body=b'{"user": {"id": 1, "name": "Shurz"}}'):
        """
        Returns (fake_first_hop, fake_request, calls). Two separate fakes
        because the five real steps go through two different transport
        functions: steps 1-4 (root GET/POST, the bridge, the token-landing
        request) all use _request_first_hop -- never auto-following a
        redirect, the same discipline TranscriptionClient.login() already
        uses and for the identical reason. Step 5 (the independent
        /api/auth/user verification) goes through whoami(), which -- like
        every other read on this client -- uses plain _request.
        """
        calls = []
        if root_post_location is None:
            root_post_location = f'{EAGLE_ROOT_HOST}/dashboard'
        if bridge_location is None:
            bridge_location = f'{TRANSLATE_HOST}/?accessToken={self.FAKE_TOKEN}'

        def fake_first_hop(url, cookie, method='GET', body=None, headers=None, **kw):
            calls.append({'url': url, 'method': method, 'body': body, 'fn': 'first_hop'})
            if url == f'{EAGLE_ROOT_HOST}/login' and method == 'GET':
                return root_get_status, '', [], root_get_body
            if url == f'{EAGLE_ROOT_HOST}/login' and method == 'POST':
                return root_post_status, root_post_location, [], root_post_body
            if url == f'{EAGLE_ROOT_HOST}/user/service/translation':
                return bridge_status, bridge_location, [], b''
            if url == bridge_location:
                return landing_status, '', list(landing_cookies), b'<html>spa shell</html>'
            raise AssertionError(f'unexpected _request_first_hop call: {method} {url}')

        def fake_request(url, cookie, method='GET', body=None, headers=None, **kw):
            calls.append({'url': url, 'method': method, 'body': body, 'fn': 'request'})
            if url.endswith('/api/auth/user'):
                return whoami_status, url, [], whoami_body
            raise AssertionError(f'unexpected _request call: {method} {url}')

        return fake_first_hop, fake_request, calls

    def _patched(self, fake_first_hop, fake_request):
        return (
            mock.patch('core.services.eagle_client._request_first_hop', side_effect=fake_first_hop),
            mock.patch('core.services.eagle_client._request', side_effect=fake_request),
        )

    # -- 1. successful complete SSO flow ------------------------------- #

    def test_a_successful_full_sso_flow_adopts_the_translation_cookie(self):
        client = TranslationClient('')
        fake_first_hop, fake_request, calls = self._fake_hops()

        p1, p2 = self._patched(fake_first_hop, fake_request)
        with p1, p2:
            client.login(FAKE_USERNAME, FAKE_PASSWORD)

        self.assertIn('translatepro_session=freshly-authed', client.cookie)
        # All five real steps happened, in order, once each.
        self.assertEqual([c['url'] for c in calls], [
            f'{EAGLE_ROOT_HOST}/login',
            f'{EAGLE_ROOT_HOST}/login',
            f'{EAGLE_ROOT_HOST}/user/service/translation',
            f'{TRANSLATE_HOST}/?accessToken={self.FAKE_TOKEN}',
            f'{TRANSLATE_HOST}/api/auth/user',
        ])

    # -- 2. root login failure ------------------------------------------ #

    def test_root_login_rejection_stops_the_whole_flow(self):
        client = TranslationClient('')
        fake_first_hop, fake_request, calls = self._fake_hops(
            root_post_status=302, root_post_location=f'{EAGLE_ROOT_HOST}/login',
            root_post_body=b'<div class="invalid-feedback">These credentials do not '
                          b'match our records.</div>',
        )

        p1, p2 = self._patched(fake_first_hop, fake_request)
        with p1, p2:
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, 'wrong-password')

        self.assertIn('do not match', str(ctx.exception))
        # Never reached the bridge, the landing request, or verification.
        self.assertEqual(len(calls), 2)

    # -- 3. bridge 401/403 ----------------------------------------------- #

    def test_bridge_401_is_rejected(self):
        client = TranslationClient('')
        fake_first_hop, fake_request, _ = self._fake_hops(bridge_status=401)
        p1, p2 = self._patched(fake_first_hop, fake_request)
        with p1, p2:
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)
        self.assertIn('401', str(ctx.exception))

    def test_bridge_403_is_rejected(self):
        client = TranslationClient('')
        fake_first_hop, fake_request, _ = self._fake_hops(bridge_status=403)
        p1, p2 = self._patched(fake_first_hop, fake_request)
        with p1, p2:
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)
        self.assertIn('403', str(ctx.exception))

    # -- 4. bridge non-302 ------------------------------------------------ #

    def test_bridge_returning_200_instead_of_a_redirect_is_rejected(self):
        client = TranslationClient('')
        fake_first_hop, fake_request, _ = self._fake_hops(bridge_status=200)
        p1, p2 = self._patched(fake_first_hop, fake_request)
        with p1, p2:
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)
        self.assertIn('did not redirect', str(ctx.exception))

    # -- 5. missing accessToken -------------------------------------------- #

    def test_bridge_redirect_with_no_access_token_is_rejected(self):
        client = TranslationClient('')
        fake_first_hop, fake_request, _ = self._fake_hops(
            bridge_location=f'{TRANSLATE_HOST}/')  # right host, no ?accessToken at all
        p1, p2 = self._patched(fake_first_hop, fake_request)
        with p1, p2:
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)
        self.assertIn('accessToken', str(ctx.exception))

    # -- 6. unexpected redirect host ---------------------------------------- #

    def test_bridge_redirect_to_an_unexpected_host_is_refused(self):
        """The origin check must be a REAL (scheme, host) comparison, not a
        string-prefix match -- this host starts with the right string
        without being the right host, which a naive check would miss."""
        client = TranslationClient('')
        fake_first_hop, fake_request, calls = self._fake_hops(
            bridge_location=f'https://translation.eaglewebcommerce.com.evil.example/'
                            f'?accessToken={self.FAKE_TOKEN}')
        p1, p2 = self._patched(fake_first_hop, fake_request)
        with p1, p2:
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)
        self.assertIn('unexpected host', str(ctx.exception))
        # And, critically, the malicious host was never actually requested.
        self.assertNotIn('evil.example', ' '.join(c['url'] for c in calls))

    # -- 7. Translation landing request failing ------------------------------ #

    def test_the_landing_request_failing_is_rejected(self):
        client = TranslationClient('')
        fake_first_hop, fake_request, _ = self._fake_hops(landing_status=500)
        p1, p2 = self._patched(fake_first_hop, fake_request)
        with p1, p2:
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)
        self.assertIn('500', str(ctx.exception))

    # -- 8. landing 200 but no Translation session ---------------------------- #

    def test_landing_200_with_no_cookie_issued_is_not_treated_as_success(self):
        client = TranslationClient('')
        fake_first_hop, fake_request, _ = self._fake_hops(
            landing_status=200, landing_cookies=())  # no Set-Cookie at all
        p1, p2 = self._patched(fake_first_hop, fake_request)
        with p1, p2:
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)
        self.assertIn('no session cookie', str(ctx.exception))

    # -- 9. /api/auth/user verification failure ------------------------------- #

    def test_verification_failure_after_a_successful_looking_handoff_is_rejected(self):
        client = TranslationClient('')
        fake_first_hop, fake_request, _ = self._fake_hops(
            whoami_status=200, whoami_body=b'{"user": null}')
        p1, p2 = self._patched(fake_first_hop, fake_request)
        with p1, p2:
            with self.assertRaises(EagleLoginRejected) as ctx:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)
        self.assertIn('did not verify', str(ctx.exception))

    # -- 10. accessToken never appears in logs, exceptions, or persisted data - #

    def test_access_token_never_appears_in_any_raised_exception(self):
        for kwargs in (
            {'bridge_status': 401},
            {'landing_status': 500},
            {'landing_cookies': ()},
            {'whoami_body': b'{"user": null}'},
        ):
            client = TranslationClient('')
            fake_first_hop, fake_request, _ = self._fake_hops(**kwargs)
            p1, p2 = self._patched(fake_first_hop, fake_request)
            with p1, p2:
                with self.assertRaises(EagleLoginRejected) as ctx:
                    client.login(FAKE_USERNAME, FAKE_PASSWORD)
            self.assertNotIn(self.FAKE_TOKEN, str(ctx.exception))

    def test_access_token_never_appears_in_any_log_line(self):
        client = TranslationClient('')
        fake_first_hop, fake_request, _ = self._fake_hops()
        p1, p2 = self._patched(fake_first_hop, fake_request)
        with self.assertLogs('eagle', level='INFO') as captured:
            with p1, p2:
                client.login(FAKE_USERNAME, FAKE_PASSWORD)
        self.assertNotIn(self.FAKE_TOKEN, '\n'.join(captured.output))

    def test_access_token_is_never_persisted_on_the_credential(self):
        """End to end, through EagleService.login() -- the actual path
        --set-login uses -- confirms the persisted cookie is Translation's
        real session only, never the bridge URL or the token."""
        credential = store_login(TRANSLATION)
        service = EagleService()
        fake_first_hop, fake_request, _ = self._fake_hops()
        p1, p2 = self._patched(fake_first_hop, fake_request)
        with p1, p2:
            service.login(TRANSLATION)

        credential.refresh_from_db()
        stored_cookie = credential.get_cookie_header()
        self.assertNotIn(self.FAKE_TOKEN, stored_cookie)
        self.assertNotIn('accessToken', stored_cookie)
        self.assertIn('translatepro_session=freshly-authed', stored_cookie)

    # -- root cookie state: multiple cookies must survive across hops -------- #

    def test_root_session_cookies_from_every_hop_accumulate_not_overwrite(self):
        """Safeguard: the root session must not assume a single cookie
        string -- a Set-Cookie on the GET, a DIFFERENT one on the POST, and
        a THIRD on the bridge call must all still be present (merged, not
        replaced) by the time the bridge request is made."""
        client = TranslationClient('')
        seen_root_cookies_at_bridge = []

        def fake_first_hop(url, cookie, method='GET', body=None, headers=None, **kw):
            if url == f'{EAGLE_ROOT_HOST}/login' and method == 'GET':
                return 200, '', ['XSRF-TOKEN=xsrf1', 'hyiprio_session=sess1'], \
                    b'<input type="hidden" name="_token" value="tok-123">'
            if url == f'{EAGLE_ROOT_HOST}/login' and method == 'POST':
                return 302, f'{EAGLE_ROOT_HOST}/dashboard', ['hyiprio_session=sess2'], b''
            if url == f'{EAGLE_ROOT_HOST}/user/service/translation':
                seen_root_cookies_at_bridge.append(cookie)
                return 302, f'{TRANSLATE_HOST}/?accessToken={self.FAKE_TOKEN}', [], b''
            if url == f'{TRANSLATE_HOST}/?accessToken={self.FAKE_TOKEN}':
                return 200, '', ['translatepro_session=authed'], b''
            raise AssertionError(url)

        def fake_request(url, cookie, method='GET', body=None, headers=None, **kw):
            return 200, url, [], b'{"user": {"name": "Shurz"}}'

        with mock.patch('core.services.eagle_client._request_first_hop', side_effect=fake_first_hop), \
             mock.patch('core.services.eagle_client._request', side_effect=fake_request):
            client.login(FAKE_USERNAME, FAKE_PASSWORD)

        self.assertEqual(len(seen_root_cookies_at_bridge), 1)
        bridge_cookie = seen_root_cookies_at_bridge[0]
        # XSRF-TOKEN from the GET must still be present...
        self.assertIn('XSRF-TOKEN=xsrf1', bridge_cookie)
        # ...and hyiprio_session must be the POST's ROTATED value, not the
        # GET's original one -- merged and updated, not just accumulated.
        self.assertIn('hyiprio_session=sess2', bridge_cookie)
        self.assertNotIn('sess1', bridge_cookie)


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
        # Recorded as a LOGIN failure, not a plain expiry: the re-login
        # looked like it succeeded and the retried request failed again
        # anyway, so a cooldown must now be active -- otherwise a stale
        # password would draw a fresh login attempt on every subsequent
        # request instead of being bounded.
        self.assertIsNotNone(credential.last_login_failure_at)
        self.assertTrue(credential.login_on_cooldown())

    def test_an_ordinary_no_retry_expiry_never_touches_the_login_cooldown(self):
        """Regression guard for the fix above: the common, everyday case
        (no login stored, or retry_on_expiry=False) must still use plain
        mark_expired and must NOT set last_login_failure_at -- that field
        means specifically "a login attempt was made and didn't hold up",
        not "a session merely lapsed"."""
        credential = store(TRANSLATION)  # cookie only, no login
        service = EagleService()

        with mock.patch.object(
            TranslationClient, 'whoami', side_effect=EagleSessionExpired('lapsed', 200),
        ):
            with self.assertRaises(EagleAuthError):
                service.check_session(TRANSLATION)

        credential.refresh_from_db()
        self.assertEqual(credential.status, EagleCredential.EXPIRED)
        self.assertIsNone(credential.last_login_failure_at)

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
# A claim is only real once Eagle's own records agree
# ===================================================================== #

class ClaimConfirmationTestCase(NoNetworkMixin, TestCase):
    """
    A REAL INCIDENT: a worker claimed and submitted a task, our side showed
    it as done, and Eagle had never registered the claim at all -- minutes
    untouched, nothing in Eagle's own queue.

    ROOT CAUSE: TranscriptionClient.claim is a plain authenticated GET whose
    only signal is "did we stay on Eagle's domain" and "was the status under
    400" -- neither tells a genuine accept apart from Eagle quietly
    rendering an ordinary 200 page saying the task is no longer available
    (already claimed by someone else on Eagle's own marketplace, a stale
    link, anything else that isn't a hard redirect-off-domain or a 4xx/5xx).

    THE FIX: `claim_task` already called `list_mine()` afterward, but only
    to fetch a deadline for display -- a failure to find the task there was
    logged and ignored. That same read is now authoritative: a genuine read
    that shows the task absent from Eagle's own held-tasks list raises,
    instead of the caller ever seeing a false success.
    """

    def test_a_claim_not_reflected_in_list_mine_is_refused(self):
        store(TRANSCRIPTION)
        service = EagleService()

        with mock.patch.object(TranscriptionClient, 'claim', return_value=True), \
             mock.patch.object(TranscriptionClient, 'list_mine', return_value=[]):
            with self.assertRaises(EagleIntegrationError):
                service.claim_task('4452', TRANSCRIPTION)

    def test_a_claim_reflected_in_list_mine_succeeds_and_carries_the_deadline(self):
        store(TRANSCRIPTION)
        service = EagleService()
        mine = [RawTask(external_id='4452', product=TRANSCRIPTION,
                        deadline_at='2026-09-24T00:00:00Z')]

        with mock.patch.object(TranscriptionClient, 'claim', return_value=True), \
             mock.patch.object(TranscriptionClient, 'list_mine', return_value=mine):
            result = service.claim_task('4452', TRANSCRIPTION)

        self.assertEqual(result['deadline_at'], '2026-09-24T00:00:00Z')

    def test_a_claim_for_a_different_task_id_does_not_count_as_confirmation(self):
        """The check matches on the SPECIFIC external_id just claimed, not
        merely "list_mine returned something" -- a held task from earlier
        must not paper over this one never landing."""
        store(TRANSCRIPTION)
        service = EagleService()
        mine = [RawTask(external_id='9999', product=TRANSCRIPTION)]

        with mock.patch.object(TranscriptionClient, 'claim', return_value=True), \
             mock.patch.object(TranscriptionClient, 'list_mine', return_value=mine):
            with self.assertRaises(EagleIntegrationError):
                service.claim_task('4452', TRANSCRIPTION)

    def test_a_failed_read_back_does_not_fail_a_claim_that_may_be_genuine(self):
        """The claim itself may already have spent real minutes -- a
        network hiccup on the FOLLOW-UP read must not manufacture a
        refusal. Only a read that succeeds and shows the task absent is
        trusted as a real negative."""
        store(TRANSCRIPTION)
        service = EagleService()

        with mock.patch.object(TranscriptionClient, 'claim', return_value=True), \
             mock.patch.object(
                 TranscriptionClient, 'list_mine',
                 side_effect=EagleHTTPError('unreachable', 502)):
            result = service.claim_task('4452', TRANSCRIPTION)

        self.assertNotIn('deadline_at', result)

    def test_the_same_confirmation_applies_to_translation_claims(self):
        """claim_task is product-agnostic -- this guards both clients with
        one check, regardless of whether each client's own status-code
        detection is itself trustworthy."""
        store(TRANSLATION)
        service = EagleService()

        with mock.patch.object(
                 TranslationClient, 'claim',
                 return_value={'accepted': True, 'minutes_balance': 40}), \
             mock.patch.object(TranslationClient, 'list_mine', return_value=[]):
            with self.assertRaises(EagleIntegrationError):
                service.claim_task('4452', TRANSLATION)


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

    def test_login_succeeding_then_whoami_failing_never_leaves_the_credential_active(self):
        """Category D. `_login_client()` not raising only means the login
        POST's own redirect/response looked like success -- it is not
        proof the session actually works. EagleService.login() must not
        report ACTIVE (or leave the row that way) on an attempt it is
        about to raise an exception for."""
        credential = store_login(TRANSLATION)  # login only, no pre-existing cookie
        service = EagleService()

        def fake_login(client_self, username, password):
            client_self.cookie = 'translatepro_session=looked-fine-but-is-not'

        with mock.patch.object(TranslationClient, 'login', fake_login), \
             mock.patch.object(TranslationClient, 'whoami',
                               side_effect=EagleSessionExpired('user: null', 200)):
            with self.assertRaises(EagleAuthError):
                service.login(TRANSLATION)

        credential.refresh_from_db()
        self.assertNotEqual(credential.status, EagleCredential.ACTIVE)
        self.assertIsNotNone(credential.last_login_failure_at)
        self.assertTrue(credential.login_on_cooldown())

    def test_login_succeeding_then_whoami_transport_error_also_records_failure(self):
        """Same as above, for the OTHER exception whoami() can raise --
        the exact shape the real incident hit (transcription panel
        returning a plain HTTP error, not a redirect-shaped expiry)."""
        credential = store_login(TRANSLATION)
        service = EagleService()

        def fake_login(client_self, username, password):
            client_self.cookie = 'translatepro_session=looked-fine-but-is-not'

        with mock.patch.object(TranslationClient, 'login', fake_login), \
             mock.patch.object(TranslationClient, 'whoami',
                               side_effect=EagleHTTPError('panel returned 403', 403)):
            with self.assertRaises(EagleIntegrationError):
                service.login(TRANSLATION)

        credential.refresh_from_db()
        self.assertNotEqual(credential.status, EagleCredential.ACTIVE)
        self.assertIsNotNone(credential.last_login_failure_at)


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
        def fake_get(url, cookie, method='GET', body=None, headers=None, **kw):
            return 200, url, [], b'<input type="hidden" name="_token" value="t">'

        # Translation: the whole SSO dance goes through _request_first_hop;
        # rejected at the root login POST (first leg), so nothing beyond it
        # is ever reached.
        def fake_translation_first_hop(url, cookie, method='GET', body=None,
                                       headers=None, **kw):
            if method == 'GET':
                return 200, '', [], b'<input type="hidden" name="_token" value="t">'
            return 302, f'{EAGLE_ROOT_HOST}/login', [], b'{"message": "no"}'

        with mock.patch('core.services.eagle_client._request_first_hop',
                        side_effect=fake_translation_first_hop):
            with self.assertRaises(EagleLoginRejected) as ctx:
                TranslationClient('').login(FAKE_USERNAME, FAKE_PASSWORD)
        self.assertNotIn(FAKE_PASSWORD, str(ctx.exception))

        # Transcription: GET via _request, POST via _request_first_hop --
        # both must be patched, or NoNetworkMixin's real-request guard
        # correctly fires instead (as it did the first time this was
        # written, before the POST leg's own mock was added here).
        def fake_transcription_post(url, cookie, method='POST', body=None,
                                    headers=None, **kw):
            return 422, '', [], b'no'

        with mock.patch('core.services.eagle_client._request', side_effect=fake_get), \
             mock.patch('core.services.eagle_client._request_first_hop',
                        side_effect=fake_transcription_post):
            with self.assertRaises(EagleLoginRejected) as ctx:
                TranscriptionClient('').login(FAKE_USERNAME, FAKE_PASSWORD)
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


# ===================================================================== #
# Cross-process session serialization -- the advisory lock
# ===================================================================== #

def _raw_connection():
    """
    A SECOND, independent connection to the exact test database Django is
    using -- not the one the ORM/test transaction uses. This is what makes
    the cross-connection tests below prove CROSS-PROCESS behaviour rather
    than merely cross-thread: `pg_advisory_xact_lock` taken on this
    connection is invisible to Django's own connection except through
    Postgres itself, exactly like eagle-cycle and a Daphne worker today.
    """
    settings_dict = connection.settings_dict
    return psycopg.connect(
        dbname=settings_dict['NAME'],
        user=settings_dict['USER'],
        password=settings_dict['PASSWORD'],
        host=settings_dict['HOST'] or 'localhost',
        port=settings_dict['PORT'] or 5432,
        autocommit=False,
    )


class RequestSerializationTestCase(NoNetworkMixin, TransactionTestCase):
    """
    Category 1 (temporal overlap) and category 4 (cookie-persistence
    race), against REAL threads -- proving timing, not just final state.

    TransactionTestCase, NOT TestCase, DELIBERATELY. A real background
    thread's DB connection is its own, separate from the main test
    thread's -- confirmed the hard way earlier this session with a
    completely different feature, and the fix there was the same one
    applied here: tests that spawn genuine threads touching the database
    need TransactionTestCase, not TestCase's atomic wrapping, or the
    thread's writes are invisible to (and not cleaned up by) the test's
    own transaction.
    """

    def _timed_operation(self, sleep_for=0.05):
        """A fake whoami() that records its own (start, end) wall-clock
        window and sleeps long enough to make overlap detectable if it
        happens -- real threads genuinely run concurrently during a
        time.sleep(), CPython's GIL notwithstanding."""
        intervals = []
        ilock = threading.Lock()

        def fake(client_self):
            start = time.monotonic()
            time.sleep(sleep_for)
            end = time.monotonic()
            with ilock:
                intervals.append((start, end))
            return EagleAccount(label='Shurz', minutes_balance=1)

        return fake, intervals

    @staticmethod
    def _run_two(call):
        threads = [threading.Thread(target=call) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

    @staticmethod
    def _overlaps(a, b):
        (s1, e1), (s2, e2) = a, b
        return s1 < e2 and s2 < e1

    def test_raw_client_calls_can_overlap_without_synchronization(self):
        """What the lock exists to prevent: two calls with NOTHING
        serializing them genuinely run at the same time."""
        fake, intervals = self._timed_operation()
        with mock.patch.object(TranslationClient, 'whoami', fake):
            self._run_two(lambda: TranslationClient(COOKIE).whoami())

        self.assertEqual(len(intervals), 2)
        self.assertTrue(self._overlaps(*intervals),
                        'expected these two unsynchronized calls to overlap')

    def test_guard_prevents_two_check_session_calls_from_overlapping(self):
        """The production path: EagleService.check_session -> _guard ->
        _locked must serialize two concurrent callers' critical sections."""
        store(TRANSLATION)
        service = EagleService()
        fake, intervals = self._timed_operation()

        with mock.patch.object(TranslationClient, 'whoami', fake):
            self._run_two(lambda: service.check_session(TRANSLATION))

        self.assertEqual(len(intervals), 2)
        self.assertFalse(self._overlaps(*intervals),
                         'the two critical sections overlapped -- the lock did '
                         'not serialize them')

    def test_serialized_calls_never_both_start_from_the_same_cookie(self):
        """Category 4. Each call rotates to a DISTINCT new cookie; the
        second call must observe the FIRST call's rotation, not the
        original snapshot both would see if unsynchronized."""
        store(TRANSLATION, cookie='translatepro_session=C1')
        service = EagleService()
        seen_cookies = []
        counter = {'n': 1}
        clock_lock = threading.Lock()

        def fake(client_self):
            with clock_lock:
                seen_cookies.append(client_self.cookie)
                counter['n'] += 1
                next_value = counter['n']
            time.sleep(0.05)
            client_self.cookie = f'translatepro_session=C{next_value}'
            client_self.cookie_rotated = True
            return EagleAccount(label='Shurz', minutes_balance=1)

        with mock.patch.object(TranslationClient, 'whoami', fake):
            self._run_two(lambda: service.check_session(TRANSLATION))

        self.assertEqual(len(seen_cookies), 2)
        self.assertNotEqual(
            seen_cookies[0], seen_cookies[1],
            'both calls started from the same cookie -- serialization did '
            'not stop the second from reading a stale snapshot')
        credential = EagleCredential.objects.get(product=TRANSLATION)
        # The persisted cookie is whichever call ran second's rotation --
        # never a torn value, and never the untouched original.
        self.assertIn(credential.get_cookie_header(), seen_cookies[::-1] + [
            f'translatepro_session=C{counter["n"]}'])

    def test_two_concurrent_translation_sso_logins_are_serialized(self):
        """Category 11. The advisory lock must cover the ENTIRE multi-hop
        SSO sequence -- root login, bridge, token landing, verification,
        persistence -- not just a single request. Exercises
        EagleService.login() directly (the real --set-login / automatic
        retry-login path, where the multi-hop dance actually lives) rather
        than re-testing TranslationClient.login()'s own internals, which
        the dedicated SSO tests above already cover: this proves the LOCK's
        coverage, standing in for however long the real dance takes with
        one sleep inside a mocked login()."""
        store_login(TRANSLATION)
        service = EagleService()
        intervals = []
        ilock = threading.Lock()

        def fake_login(client_self, username, password):
            start = time.monotonic()
            time.sleep(0.05)
            client_self.cookie = 'translatepro_session=authed'
            end = time.monotonic()
            with ilock:
                intervals.append((start, end))

        def fake_whoami(client_self):
            return EagleAccount(label='Shurz', minutes_balance=1)

        with mock.patch.object(TranslationClient, 'login', fake_login), \
             mock.patch.object(TranslationClient, 'whoami', fake_whoami):
            self._run_two(lambda: service.login(TRANSLATION))

        self.assertEqual(len(intervals), 2)
        self.assertFalse(self._overlaps(*intervals),
                         'two concurrent SSO logins overlapped -- the lock did '
                         'not serialize the full multi-hop sequence')


class AdvisoryLockCrossConnectionTestCase(NoNetworkMixin, TransactionTestCase):
    """
    Category 2 and 3. A SECOND, genuinely independent PostgreSQL connection
    holds the lock; the production code, using Django's own (different)
    connection, must be unable to acquire it until the holder releases --
    proving this is a real cross-connection (and so cross-process) lock,
    not merely a Python-level one that only happens to work within a
    single process by coincidence.
    """

    def setUp(self):
        super().setUp()
        self.raw = _raw_connection()
        self.addCleanup(self.raw.close)

    def _hold_lock_on_raw_connection(self, product):
        with self.raw.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', [f'eagle:{product}'])
        # BLOCKING form: returns only once acquired. self.raw's transaction
        # is left open (no commit/rollback yet), so the lock is held until
        # the test explicitly releases it.

    def _release_raw_lock(self):
        self.raw.rollback()  # pg_advisory_xact_lock releases at transaction end

    def test_a_second_connection_cannot_acquire_the_held_lock(self):
        store(TRANSCRIPTION)
        self._hold_lock_on_raw_connection(TRANSCRIPTION)
        service = EagleService()

        with mock.patch('apps.workforce.eagle_impl.EAGLE_LOCK_TIMEOUT_SECONDS', 0.5), \
             mock.patch('apps.workforce.eagle_impl.EAGLE_LOCK_POLL_INTERVAL_SECONDS', 0.05):
            with self.assertRaises(EagleBusy):
                service.check_session(TRANSCRIPTION)

    def test_releasing_the_raw_lock_lets_the_production_code_proceed(self):
        store(TRANSCRIPTION)
        self._hold_lock_on_raw_connection(TRANSCRIPTION)
        service = EagleService()

        def fake(client_self):
            return EagleAccount(label='Eagle Transcribe', raw={})

        result_holder = {}

        def attempt():
            with mock.patch('apps.workforce.eagle_impl.EAGLE_LOCK_TIMEOUT_SECONDS', 3), \
                 mock.patch('apps.workforce.eagle_impl.EAGLE_LOCK_POLL_INTERVAL_SECONDS', 0.05), \
                 mock.patch.object(TranscriptionClient, 'whoami', fake):
                result_holder['result'] = service.check_session(TRANSCRIPTION)

        thread = threading.Thread(target=attempt)
        thread.start()
        time.sleep(0.3)  # let the thread block on the held lock for a moment
        self._release_raw_lock()
        thread.join(timeout=10)

        self.assertEqual(result_holder.get('result', {}).get('account'), 'Eagle Transcribe')

    def test_timeout_raises_eaglebusy_not_hang_and_touches_no_credential_state(self):
        """Category 3, in full: fails after the configured timeout with the
        dedicated typed error, and -- because the lock acquisition happens
        BEFORE the credential is even read -- never marks it expired or
        records a login failure."""
        credential = store_with_login(TRANSCRIPTION)
        original_status = credential.status
        self._hold_lock_on_raw_connection(TRANSCRIPTION)
        service = EagleService()

        started = time.monotonic()
        with mock.patch('apps.workforce.eagle_impl.EAGLE_LOCK_TIMEOUT_SECONDS', 0.4), \
             mock.patch('apps.workforce.eagle_impl.EAGLE_LOCK_POLL_INTERVAL_SECONDS', 0.05):
            with self.assertRaises(EagleBusy):
                service.check_session(TRANSCRIPTION)
        elapsed = time.monotonic() - started

        # Bounded, not instant and not indefinite.
        self.assertGreaterEqual(elapsed, 0.35)
        self.assertLess(elapsed, 3.0)

        credential.refresh_from_db()
        self.assertEqual(credential.status, original_status)
        self.assertIsNone(credential.last_login_failure_at)
        self.assertEqual(credential.last_failure_detail, '')

    def test_timeout_on_login_also_touches_no_credential_state(self):
        """Same guarantee for EagleService.login() (--set-login's path),
        not just the automatic _guard retry path."""
        credential = store_login(TRANSCRIPTION)
        self._hold_lock_on_raw_connection(TRANSCRIPTION)
        service = EagleService()

        with mock.patch('apps.workforce.eagle_impl.EAGLE_LOCK_TIMEOUT_SECONDS', 0.3), \
             mock.patch('apps.workforce.eagle_impl.EAGLE_LOCK_POLL_INTERVAL_SECONDS', 0.05):
            with self.assertRaises(EagleBusy):
                service.login(TRANSCRIPTION)

        credential.refresh_from_db()
        self.assertIsNone(credential.last_login_failure_at)
        self.assertEqual(credential.last_failure_detail, '')
