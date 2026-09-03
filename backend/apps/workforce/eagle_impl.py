"""
The real Eagle integration, behind the Phase 2 interface.

WHAT THIS FILE IS FOR. `eagle.py` defines what the application needs;
`core.services.eagle_client` knows how to talk HTTP to two different Eagle
products; this joins them to Django — reading the encrypted credential,
recording session health, and normalising two dissimilar platforms into the
one shape the rest of the system codes against.

NOTHING HERE SPENDS MONEY BY ACCIDENT. `claim_task` and `submit_task` are the
only methods that change anything on Eagle: claiming deducts purchased
minutes and starts a one-hour deadline, and submitting is graded work. Both
check the credential, both log, and neither is reachable from a worker-facing
endpoint without passing through the workforce service layer first.

CREDENTIALS NEVER TRAVEL OUTWARD. `get_task_media` returns a URL only when
that URL is genuinely public; when the platform requires the session, the
caller is told `needs_session` and must fetch through the backend. No method
here returns a cookie, and none puts one in an exception message.
"""
import logging
from typing import Dict, List, Optional

from core.services.eagle_client import (
    EagleHTTPError, EagleSessionExpired, TranscriptionClient, TranslationClient,
)

from .credentials import TRANSCRIPTION, TRANSLATION, get_credential
from .eagle import (
    EagleAuthError, EagleIntegrationError, EagleMedia, EagleNotConfigured,
    EagleSubmissionResult, EagleTask, EagleTaskNotFound, EagleIntegrationService,
)

logger = logging.getLogger('eagle')

#: Eagle's own status vocabulary -> our Task states. Taken from their UI code
#: and observed rows, NOT invented. Anything unmapped stays unmapped rather
#: than being guessed into a state that drives behaviour.
EXTERNAL_STATUS_MAP = {
    # translation
    'available': 'available',
    'claimed': 'claimed',
    'processing': 'processing',
    'submitted': 'submitted',
    'completed': 'approved',
    'rejected': 'rejected',
    'failed': 'failed',
    # transcription (displayed as "Pending" but stored as IN_PROGRESS)
    'IN_PROGRESS': 'available',
}


class EagleService(EagleIntegrationService):
    """The configured implementation. One instance serves both products."""

    name = 'eagle'

    # -- plumbing -------------------------------------------------------- #

    def _client(self, source: str):
        product = (source or '').strip().lower()
        if product not in (TRANSCRIPTION, TRANSLATION):
            raise EagleIntegrationError(
                f'Unknown Eagle product {source!r}.',
                'That external platform is not configured.',
            )
        credential = get_credential(product)
        if credential is None or not credential.is_usable:
            raise EagleNotConfigured(
                f'No usable credential stored for {product}.',
            )
        cookie = credential.get_cookie_header()
        if not cookie:
            raise EagleNotConfigured(f'Credential for {product} could not be read.')

        client_cls = TranscriptionClient if product == TRANSCRIPTION else TranslationClient
        return client_cls(cookie), credential

    def _guard(self, credential, operation, client=None):
        """
        Run an Eagle call, translating transport failures into our error
        vocabulary and keeping the credential's health record current.

        A session that has lapsed is recorded as EXPIRED so the operator sees
        it on the dashboard rather than discovering it through a worker's
        failed claim.

        IT ALSO PERSISTS A ROTATED SESSION. Eagle reissues a cookie on
        essentially every request. The client adopts the new value for its
        own lifetime, but without writing it back the rotation dies with the
        process and the next one starts by replaying a session the server
        has long since moved past. That is a session which "expires" while
        nobody was away.
        """
        try:
            result = operation()
        except EagleSessionExpired as exc:
            credential.mark_expired(exc.detail)
            logger.error('[EAGLE] session expired for %s: %s', credential.product, exc.detail)
            raise EagleAuthError(exc.detail) from exc
        except EagleHTTPError as exc:
            logger.warning('[EAGLE] %s call failed: %s', credential.product, exc.detail)
            raise EagleIntegrationError(exc.detail) from exc

        # After a successful call, not before: a cookie handed out by a
        # request that then failed is not one worth keeping.
        if client is not None and getattr(client, 'cookie_rotated', False):
            try:
                if credential.rotate_cookie(client.cookie):
                    logger.info('[EAGLE] persisted a rotated %s session',
                                credential.product)
                client.cookie_rotated = False
            except Exception:
                # Never let bookkeeping fail a call that worked.
                logger.exception('[EAGLE] could not persist rotated session')
        return result

    def is_configured(self) -> bool:
        return any(
            (get_credential(p) is not None and get_credential(p).is_usable)
            for p in (TRANSCRIPTION, TRANSLATION)
        )

    # -- health ---------------------------------------------------------- #

    def check_session(self, source: str) -> Dict:
        """
        Is the stored session still good?

        Called by the poller every cycle, which doubles as the keep-alive:
        Laravel idles a session out after ~120 minutes and we are never
        idle that long.
        """
        client, credential = self._client(source)
        account = self._guard(credential, client.whoami, client=client)
        credential.mark_verified(
            account_label=account.label, minutes_balance=account.minutes_balance,
        )
        return {
            'product': source,
            'status': credential.status,
            'account': account.label,
            'minutes_balance': account.minutes_balance,
        }

    def minutes_balance(self, source: str) -> Optional[int]:
        """
        Purchased minutes remaining. The real throughput cap: no minutes
        means no claiming, regardless of how many workers are free.
        """
        credential = get_credential((source or '').lower())
        return credential.minutes_balance if credential else None

    # -- discovery -------------------------------------------------------- #

    def list_available_tasks(self, source: str = '') -> List[EagleTask]:
        client, credential = self._client(source)
        rows = self._guard(credential, client.list_available, client=client)
        return [self._to_task(r) for r in rows if not r.claimed]

    def list_my_tasks(self, source: str = '') -> List[EagleTask]:
        client, credential = self._client(source)
        if not hasattr(client, 'list_mine'):
            # Both products now have a verified awarded endpoint, so this is
            # a fallback for a client that does not: report what is knowable
            # from the main list rather than scraping blind.
            rows = self._guard(credential, client.list_available, client=client)
            return [self._to_task(r) for r in rows if r.claimed]
        rows = self._guard(credential, client.list_mine)
        return [self._to_task(r) for r in rows]

    def get_task(self, external_id: str, source: str = '') -> EagleTask:
        client, credential = self._client(source)
        if not hasattr(client, 'get_task'):
            for task in self.list_available_tasks(source):
                if task.external_id == str(external_id):
                    return task
            raise EagleTaskNotFound(f'{source} task {external_id} not in the available list.')
        return self._to_task(self._guard(credential, lambda: client.get_task(external_id)))

    # -- media ------------------------------------------------------------ #

    def get_task_media(self, external_id: str, source: str = '') -> EagleMedia:
        """
        Where the audio is, and whether our session is needed to fetch it.

        The two products differ and the difference is load-bearing:
        transcription serves the mp3 with no credentials at all, translation
        requires the session. A caller must never assume it can hand this
        URL to a browser -- check `headers`.
        """
        task = self.get_task(external_id, source)
        raw = task.raw or {}
        needs_session = bool(raw.get('media_needs_session', True))
        url = raw.get('media_url') or ''
        if not url:
            raise EagleTaskNotFound(f'No media on {source} task {external_id}.')
        media = EagleMedia(url=url, content_type='audio/mpeg')
        if needs_session:
            # Deliberately NOT populated here. get_authenticated_media is the
            # explicit door for the credential-bearing form, so a caller
            # cannot pick up headers it then has to remember not to leak.
            media.headers = {}
        return media

    def get_authenticated_media(self, external_id: str, source: str = '') -> EagleMedia:
        """
        Media plus the session header. SERVER-SIDE CALLERS ONLY.

        The result of this must never reach a serializer, a template or a
        worker's browser. It exists so the backend can stream the audio
        itself and serve it onward through its own authorised endpoint.
        """
        media = self.get_task_media(external_id, source)
        _, credential = self._client(source)
        cookie = credential.get_cookie_header()
        if cookie:
            media.headers = {'Cookie': cookie}
        return media

    # -- mutations: these cost money -------------------------------------- #

    def claim_task(self, external_id: str, source: str = '') -> Dict:
        """
        Accept a task. SPENDS MINUTES and starts the one-hour deadline.

        Returns whatever the platform reports, including the new balance
        where it gives one -- translation does, and it should be persisted
        rather than inferred.
        """
        client, credential = self._client(source)
        logger.info('[EAGLE] claiming %s task %s', source, external_id)
        result = self._guard(credential, lambda: client.claim(external_id))

        if isinstance(result, dict) and result.get('minutes_balance') is not None:
            credential.minutes_balance = result['minutes_balance']
            credential.save(update_fields=['minutes_balance', 'updated_at'])
        return result if isinstance(result, dict) else {'accepted': bool(result)}

    def submit_task(self, external_id: str, content: str, source: str = '',
                    metadata: Optional[Dict] = None) -> EagleSubmissionResult:
        """
        Submit finished work.

        `content` is the rendered Eagle document from
        `core.services.export_formats` -- validated byte-for-byte against
        accepted submissions. It is NOT built in a browser.
        """
        client, credential = self._client(source)
        product = (source or '').lower()
        logger.info('[EAGLE] submitting %s task %s (%d chars)',
                    product, external_id, len(content or ''))

        if product == TRANSLATION:
            raw = self._guard(credential, lambda: client.submit(external_id, content))
            return EagleSubmissionResult(
                accepted=True, reference=str(external_id),
                message='Submitted for review.', raw=raw or {},
            )

        raw = self._guard(credential, lambda: client.submit(external_id, content))
        return EagleSubmissionResult(
            accepted=raw.get('status', 0) < 400,
            reference=str(external_id),
            message='Submitted for review.',
            raw=raw or {},
        )

    # -- normalisation ----------------------------------------------------- #

    @staticmethod
    def _to_task(raw) -> EagleTask:
        kind = 'translation' if raw.product == TRANSLATION else 'transcription'
        return EagleTask(
            external_id=raw.external_id,
            source=raw.product,
            title=raw.title,
            kind=kind,
            source_language=raw.language,
            target_language=raw.target_language,
            duration_seconds=int(raw.duration_seconds) if raw.duration_seconds else None,
            project=raw.project or '',
            minutes_required=raw.minutes_required,
            deadline_at=raw.deadline_at or '',
            page_url=raw.page_url,
            raw={
                'external_status': raw.status,
                'mapped_status': EXTERNAL_STATUS_MAP.get(raw.status, ''),
                'media_url': raw.media_url,
                'media_needs_session': raw.media_needs_session,
                'minutes_required': raw.minutes_required,
                'payout': raw.payout,
                'deadline_at': raw.deadline_at,
                'claimed': raw.claimed,
            },
        )
