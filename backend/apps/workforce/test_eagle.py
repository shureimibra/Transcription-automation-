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
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from core.services.eagle_client import (
    merge_set_cookies,
    xsrf_header,
    EagleSessionExpired, RawTask, TranscriptionClient, TranslationClient,
)

from .credentials import TRANSCRIPTION, TRANSLATION, EagleCredential
from .eagle import EagleAuthError, EagleNotConfigured
from .eagle_impl import EagleService

COOKIE = 'translatepro_session=SUPERSECRET; cf_clearance=ALSOSECRET'


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
