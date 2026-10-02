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
import contextlib
import logging
import os
import time
from typing import Dict, List, Optional

from django.db import connection, transaction

from core.services.eagle_client import (
    EagleHTTPError, EagleLoginRejected, EagleSessionExpired,
    TranscriptionClient, TranslationClient,
)

from .credentials import TRANSCRIPTION, TRANSLATION, get_credential
from .eagle import (
    EagleAuthError, EagleBusy, EagleIntegrationError, EagleLoginFailed,
    EagleMedia, EagleNotConfigured, EagleSubmissionResult, EagleTask,
    EagleTaskNotFound, EagleIntegrationService,
)

logger = logging.getLogger('eagle')

#: How long to wait for another process's Eagle-session critical section to
#: finish before giving up with EagleBusy. Bounded deliberately -- see
#: EagleService._locked's docstring for why an unbounded wait is not safe
#: here (a web request holding a claim button, or eagle-cycle itself,
#: cannot be left hanging on contention that might never clear).
EAGLE_LOCK_TIMEOUT_SECONDS = float(os.environ.get('EAGLE_LOCK_TIMEOUT_SECONDS', '10'))

#: How often to retry pg_try_advisory_xact_lock while waiting. Short: the
#: lock is only ever held for the duration of one Eagle HTTP round-trip
#: (or two, on a retry-with-relogin), so a fast poll costs little and keeps
#: the wait close to however long the actual holder needed.
EAGLE_LOCK_POLL_INTERVAL_SECONDS = float(
    os.environ.get('EAGLE_LOCK_POLL_INTERVAL_SECONDS', '0.1'))

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
        if credential is None:
            raise EagleNotConfigured(f'No credential stored for {product}.')

        # A credential marked EXPIRED with NO stored login is exactly what
        # is_usable exists to stop: only a human re-pasting a cookie can
        # recover it, so refusing here is correct and unchanged from before
        # this feature existed.
        #
        # BUT one WITH a stored login must still get through, even while
        # EXPIRED -- that status is exactly what a rejected attempt (or a
        # cooldown) leaves it in, and _guard's retry-on-expiry branch is
        # what is supposed to get the next chance to fix it. Refusing here
        # would mean automatic re-login only ever works WITHIN the single
        # call that first hit the expiry, never again on any later poller
        # cycle -- which defeats the entire feature.
        if not credential.is_usable and not credential.has_login:
            raise EagleNotConfigured(f'No usable credential stored for {product}.')

        cookie = credential.get_cookie_header()
        if not cookie and not credential.has_login:
            raise EagleNotConfigured(f'Credential for {product} could not be read.')

        client_cls = TranscriptionClient if product == TRANSCRIPTION else TranslationClient
        return client_cls(cookie), credential

    @staticmethod
    def _client_cls(source: str):
        """Which client class a product uses, without touching the
        database or building a real client -- for callers (list_my_tasks,
        get_task) that only need to know whether a method EXISTS on it."""
        product = (source or '').strip().lower()
        return TranscriptionClient if product == TRANSCRIPTION else TranslationClient

    @contextlib.contextmanager
    def _locked(self, product: str):
        """
        Cross-process mutual exclusion for one Eagle product's ENTIRE
        session critical section: read the credential, talk to Eagle,
        absorb whatever cookie comes back, and persist it -- all of it,
        not just the final write.

        WHY THE WRITE ALONE IS NOT ENOUGH. A lock taken only around
        `credential.rotate_cookie(...)` serializes which write lands last,
        but by then both requests have ALREADY reached Eagle carrying the
        SAME starting cookie -- whatever Eagle's own session store does
        with two concurrent regenerate-triggering requests against one
        session has already happened, entirely outside anything a later
        DB lock can influence. The only construction that prevents two
        requests from ever being sent with the same stale cookie is one
        where a second caller cannot even START its request until the
        first caller's entire read-request-absorb-persist cycle has
        completed. That is the boundary this wraps.

        WHY A DATABASE ADVISORY LOCK, NOT threading.Lock. eagle-cycle and
        the Django/Daphne process serving worker claim/submit requests are
        two separate OS processes on the same box TODAY -- confirmed via
        apps/workforce/eagle_claim.py and eagle_submit.py, both reachable
        from apps/workforce/worker_views.py. A process-local lock is
        invisible across that boundary. `pg_try_advisory_xact_lock` is
        mediated by Postgres itself, so it works identically whether the
        other holder is a warm-pool thread in this same process or a
        Daphne worker in a different one.

        WHY TRANSACTION-SCOPED, NOT SESSION-SCOPED. `pg_advisory_xact_lock`
        releases automatically at the end of the current transaction --
        commit, rollback, or the connection simply dying -- with no manual
        unlock call required. A session-scoped lock (`pg_advisory_lock`)
        would need an explicit, always-reached `pg_advisory_unlock`, and a
        killed process (or a bug on some exception path) leaves it held
        forever. The transaction-scoped form cannot leak that way.

        WHY POLLED, NOT A BLOCKING `pg_advisory_xact_lock`. A caller stuck
        waiting forever on contention that never clears -- a wedged
        process still holding its transaction open -- is worse than a
        caller that fails cleanly and can be retried. `_try_` plus a
        bounded poll gives EagleBusy instead of an indefinite hang.

        CALLERS MUST DO ALL CREDENTIAL-MUTATING WORK **INSIDE** THIS BLOCK,
        AND MUST NOT LET AN EXCEPTION ESCAPE IT TO SIGNAL FAILURE. Because
        this wraps a real `transaction.atomic()`, an exception propagating
        out of the `with` block rolls back everything written inside it --
        including a `mark_expired()`/`record_login_failure()` call that was
        made specifically to RECORD that failure. `_guard()` and `login()`
        both handle this the same way: construct the exception to raise,
        let the `with` block exit normally (so the mutation commits), and
        raise only after it has closed.
        """
        deadline = time.monotonic() + EAGLE_LOCK_TIMEOUT_SECONDS
        with transaction.atomic():
            while True:
                with connection.cursor() as cursor:
                    cursor.execute(
                        'SELECT pg_try_advisory_xact_lock(hashtext(%s))',
                        [f'eagle:{product}'],
                    )
                    (acquired,) = cursor.fetchone()
                if acquired:
                    break
                if time.monotonic() >= deadline:
                    raise EagleBusy(
                        f'Timed out after {EAGLE_LOCK_TIMEOUT_SECONDS:.0f}s waiting '
                        f'for the {product} Eagle session lock.',
                    )
                time.sleep(EAGLE_LOCK_POLL_INTERVAL_SECONDS)
            yield

    def _guard(self, source, operation_factory, retry_on_expiry=False):
        """
        Run an Eagle call, translating transport failures into our error
        vocabulary and keeping the credential's health record current.

        NOW TAKES `source` (the product string) RATHER THAN A PRE-BUILT
        `credential`/`client`. The credential read and client construction
        happen INSIDE the lock below, not before it -- reading the cookie
        before acquiring the lock would defeat the whole point: a second
        caller could still be mid-request with the pre-lock snapshot while
        this one is running. See `_locked`'s docstring for the full reasoning.

        `operation_factory` takes the ACTIVE CLIENT and invokes the one
        method this call cares about (`lambda c: c.whoami()`, etc.) rather
        than closing over `client` directly. That is what lets a retry
        after re-authentication run the SAME operation against a FRESH
        client built from the new cookie, instead of replaying a closure
        bound to a client whose session is already known to be dead.

        RETRY IS OPT-IN PER CALL SITE, AND BOUNDED TO EXACTLY ONE ATTEMPT.
        `retry_on_expiry=True` is only ever passed for read operations
        (whoami/list_available/list_mine/get_task) -- claim and submit
        spend money and start deadlines, and this file's own module
        docstring is explicit that nothing here should spend by accident.
        Retrying one of those after a re-login risks the FIRST attempt
        having actually landed on Eagle before the expiry signal was read;
        a blind retry could double it. They stay on the original,
        unconditional behaviour: one attempt, and a session that fails it
        is reported, not retried. The advisory lock below is pure
        synchronization -- it changes none of this; claim and submit are
        exactly as un-retried as they were before it existed.

        A credential with no stored login (`has_login` False) is entirely
        unaffected by the retry logic -- the retry branch never engages, so
        the manual-cookie-only fallback keeps behaving exactly as it always
        has, byte for byte.

        `attempted_relogin` DISTINGUISHES TWO DIFFERENT SHAPES OF "still
        expired" AT THE FINAL FAILURE POINT. The ordinary case -- no retry
        configured, or none available -- reaches `retries_remaining <= 0`
        having never touched login at all; that is a plain expiry and
        `mark_expired` is exactly right. But if a re-login just happened
        and the RETRIED operation fails again anyway, that is different
        information: the login looked like it succeeded and evidently
        didn't actually restore a working session. Recording that as an
        ordinary expiry would leave the cooldown unset, and a stale
        password would draw a fresh login attempt on every subsequent
        request rather than being bounded by it -- so this case is
        recorded as a login failure instead, the same way a login Eagle
        rejects outright already is.

        WHY EVERY FAILURE BRANCH BUILDS AN EXCEPTION AND `break`S INSTEAD OF
        RAISING IMMEDIATELY. `_locked()` wraps this whole method in a real
        `transaction.atomic()` for the advisory lock's sake. An exception
        propagating out of that block rolls back everything written inside
        it -- including the very `mark_expired()`/`record_login_failure()`
        call made to RECORD the failure being reported. Raising immediately,
        the way this used to, would silently discard that bookkeeping on
        every single failure path. Instead each branch commits its
        credential mutation, stashes the exception to raise, and `break`s;
        the actual `raise` happens after `_locked()`'s `with` block has
        already exited normally and committed.
        """
        product = (source or '').strip().lower()
        if product not in (TRANSCRIPTION, TRANSLATION):
            raise EagleIntegrationError(
                f'Unknown Eagle product {source!r}.',
                'That external platform is not configured.',
            )

        pending_exception = None
        result = None

        with self._locked(product):
            client, credential = self._client(source)
            retries_remaining = 1 if (retry_on_expiry and credential.has_login) else 0
            attempted_relogin = False

            while True:
                try:
                    result = operation_factory(client)
                    break
                except EagleSessionExpired as exc:
                    if retries_remaining <= 0:
                        if attempted_relogin:
                            detail = (f'Re-login appeared to succeed but the retried '
                                     f'request failed again: {exc.detail}')
                            credential.record_login_failure(detail)
                            logger.error(
                                '[EAGLE] %s re-login did not actually restore a '
                                'working session: %s', credential.product, exc.detail)
                        else:
                            credential.mark_expired(exc.detail)
                            logger.error('[EAGLE] session expired for %s: %s',
                                        credential.product, exc.detail)
                        pending_exception = EagleAuthError(exc.detail)
                        pending_exception.__cause__ = exc
                        break

                    if credential.login_on_cooldown():
                        logger.warning(
                            '[EAGLE] %s session expired and automatic re-login is '
                            'on cooldown after a recent rejection; not retrying',
                            credential.product)
                        credential.mark_expired(exc.detail)
                        pending_exception = EagleAuthError(exc.detail)
                        pending_exception.__cause__ = exc
                        break

                    # Consumed unconditionally, before the attempt: whatever
                    # happens next (login itself fails, or the retried
                    # operation fails again), this loop never runs a third time.
                    retries_remaining -= 1

                    try:
                        client = self._login_client(credential)
                    except EagleLoginFailed as login_exc:
                        credential.record_login_failure(str(login_exc))
                        logger.error('[EAGLE] automatic re-login failed for %s: %s',
                                    credential.product, login_exc)
                        pending_exception = login_exc
                        break

                    credential.record_login_success(client.cookie)
                    attempted_relogin = True
                    logger.info(
                        '[EAGLE] re-authenticated %s after session expiry; '
                        'retrying the read once', credential.product)
                    continue
                except EagleHTTPError as exc:
                    logger.warning('[EAGLE] %s call failed: %s', credential.product, exc.detail)
                    pending_exception = EagleIntegrationError(exc.detail)
                    pending_exception.__cause__ = exc
                    break

            # After a successful call, not before: a cookie handed out by a
            # request that then failed is not one worth keeping. Also
            # skipped when there is a pending_exception for the same reason
            # -- the block above already means this call did not succeed.
            if (pending_exception is None and client is not None
                    and getattr(client, 'cookie_rotated', False)):
                try:
                    if credential.rotate_cookie(client.cookie):
                        logger.info('[EAGLE] persisted a rotated %s session',
                                    credential.product)
                    client.cookie_rotated = False
                except Exception:
                    # Never let bookkeeping fail a call that worked.
                    logger.exception('[EAGLE] could not persist rotated session')

        if pending_exception is not None:
            raise pending_exception
        return result

    def _login_client(self, credential):
        """
        Perform a real login for `credential`'s product and return a fresh,
        authenticated client. Raises EagleLoginFailed on any failure --
        rejected credentials and transport failures alike -- since neither
        recovers by looping (see _guard's docstring).

        Deliberately does NOT touch the credential's cooldown/success state
        itself; both callers (_guard's retry branch and the public `login`
        below, used by --set-login) need that recorded with their own
        framing (an automatic retry's rejection vs a manual verification's),
        so it stays their responsibility, not this helper's.
        """
        username, password = credential.get_login()
        if not username or not password:
            raise EagleLoginFailed('No usable login credentials stored.')
        client_cls = TranscriptionClient if credential.product == TRANSCRIPTION else TranslationClient
        client = client_cls('')
        try:
            client.login(username, password)
        except EagleHTTPError as exc:
            # Covers EagleLoginRejected (a clear "no") and any other
            # transport failure during login (timeout, 5xx, DNS) alike --
            # from here, both mean "re-login did not produce a session".
            raise EagleLoginFailed(exc.detail) from exc
        return client

    def login(self, source: str) -> Dict:
        """
        Perform and verify a real login for `source`, independent of the
        automatic retry path above -- this is what `eagle_credential
        --set-login` calls right after storing new credentials, so the
        operator finds out immediately whether they work rather than
        waiting for the next session expiry to discover it.

        DELIBERATELY BYPASSES THE COOLDOWN. A fresh --set-login is a
        deliberate new attempt by a human who just typed a (presumably
        corrected) password, not the automatic retry loop the cooldown
        exists to bound.

        record_login_success IS ONLY CALLED AFTER whoami() ALSO SUCCEEDS,
        DELIBERATELY. `_login_client()` returning without raising only
        means the login POST's own redirect looked like success -- it is
        not, by itself, proof the resulting session actually works (see
        TranscriptionClient.login's docstring on why that redirect alone
        can be misleading). Persisting success before that second check
        would leave the credential marked ACTIVE, with no cooldown, on an
        attempt this method is about to report as a FAILURE to the
        caller -- exactly backwards. Verifying first means the stored
        state can never contradict what --set-login just told the operator.

        LOCKED THE SAME WAY `_guard` IS, AND FOR THE SAME REASON: a manual
        --set-login is exactly as capable of racing eagle-cycle or a
        worker's claim/submit over this product's session as any automatic
        call is -- see `_locked`'s docstring. Every failure branch below
        follows `_guard`'s build-then-break discipline too: the credential
        mutation must commit before the exception it explains is raised,
        which an immediate `raise` inside `_locked`'s transaction would
        instead roll back.
        """
        product = (source or '').strip().lower()
        if product not in (TRANSCRIPTION, TRANSLATION):
            raise EagleIntegrationError(
                f'Unknown Eagle product {source!r}.',
                'That external platform is not configured.',
            )

        pending_exception = None
        outcome = None

        with self._locked(product):
            credential = get_credential(product)
            if credential is None or not credential.has_login:
                raise EagleNotConfigured(f'No login credentials stored for {product}.')

            try:
                client = self._login_client(credential)
            except EagleLoginFailed as exc:
                credential.record_login_failure(str(exc))
                pending_exception = exc
            else:
                try:
                    account = client.whoami()
                except EagleSessionExpired as exc:
                    # Logged in, but the very next call says otherwise --
                    # worth surfacing as its own thing rather than a plain
                    # expiry, and recorded the same way a rejected login
                    # is: this attempt did not produce a working session,
                    # whatever the redirect looked like.
                    detail = f'Login succeeded but the session did not verify: {exc.detail}'
                    credential.record_login_failure(detail)
                    pending_exception = EagleAuthError(detail)
                    pending_exception.__cause__ = exc
                except EagleHTTPError as exc:
                    credential.record_login_failure(
                        f'Login succeeded but verification failed: {exc.detail}')
                    pending_exception = EagleIntegrationError(exc.detail)
                    pending_exception.__cause__ = exc
                else:
                    credential.record_login_success(client.cookie)
                    credential.mark_verified(account_label=account.label,
                                             minutes_balance=account.minutes_balance)
                    outcome = {
                        'product': product,
                        'account': account.label,
                        'minutes_balance': account.minutes_balance,
                    }

        if pending_exception is not None:
            raise pending_exception
        return outcome

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
        account = self._guard(source, lambda c: c.whoami(), retry_on_expiry=True)
        # Re-fetched rather than threaded through _guard: mark_verified only
        # touches status/label/minutes, never cookie_encrypted, so it does
        # not need to run inside the session lock -- see _locked's docstring
        # for exactly which fields that lock exists to protect.
        credential = get_credential((source or '').strip().lower())
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
        rows = self._guard(source, lambda c: c.list_available(), retry_on_expiry=True)
        return [self._to_task(r) for r in rows if not r.claimed]

    def list_my_tasks(self, source: str = '') -> List[EagleTask]:
        if not hasattr(self._client_cls(source), 'list_mine'):
            # Both products now have a verified awarded endpoint, so this is
            # a fallback for a client that does not: report what is knowable
            # from the main list rather than scraping blind.
            rows = self._guard(source, lambda c: c.list_available(), retry_on_expiry=True)
            return [self._to_task(r) for r in rows if r.claimed]
        rows = self._guard(source, lambda c: c.list_mine(), retry_on_expiry=True)
        return [self._to_task(r) for r in rows]

    def get_task(self, external_id: str, source: str = '') -> EagleTask:
        if not hasattr(self._client_cls(source), 'get_task'):
            for task in self.list_available_tasks(source):
                if task.external_id == str(external_id):
                    return task
            raise EagleTaskNotFound(f'{source} task {external_id} not in the available list.')
        row = self._guard(source, lambda c: c.get_task(external_id), retry_on_expiry=True)
        return self._to_task(row)

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
        rather than inferred -- PLUS `deadline_at`, read back separately
        below because neither product's claim response can be trusted to
        carry it: transcription's `claim()` is a bare redirect with no body
        at all, and translation's own `{minutes_balance, task}` shape has
        never actually been inspected for a nested deadline. Without this,
        the clock the rest of the system treats as the highest-stakes
        number on the platform was starting on Eagle's side and simply
        never being recorded on ours -- a worker could hold a task for an
        hour with no timer ever appearing, because the one write that could
        have populated it discarded the value.
        """
        logger.info('[EAGLE] claiming %s task %s', source, external_id)
        # retry_on_expiry stays False (the default): this spends minutes and
        # starts a deadline, see the module docstring and _guard's own. The
        # session lock changes none of that -- it is synchronization only.
        result = self._guard(source, lambda c: c.claim(external_id))

        if isinstance(result, dict) and result.get('minutes_balance') is not None:
            # A plain bookkeeping figure, not the cookie -- fetched fresh
            # rather than threaded through _guard for the same reason
            # check_session's mark_verified is, see its comment.
            credential = get_credential((source or '').strip().lower())
            credential.minutes_balance = result['minutes_balance']
            credential.save(update_fields=['minutes_balance', 'updated_at'])
        result = result if isinstance(result, dict) else {'accepted': bool(result)}

        # A REAL, INDEPENDENT CHECK, NOT JUST A DEADLINE LOOKUP.
        #
        # THE FALSE-POSITIVE CLAIM THIS CATCHES: TranscriptionClient.claim is
        # a plain authenticated GET, and its only signal is "did we land off
        # Eagle's domain" plus "was the status under 400" -- neither of those
        # can tell a genuine accept apart from Eagle quietly rendering an
        # ordinary 200 page that says the task is no longer available. A real
        # incident: a worker's claim, submission and "awaiting review" state
        # all looked normal on our side while Eagle had never registered the
        # task as claimed at all -- minutes untouched, nothing in Eagle's own
        # queue. Read-level bugs like that one are invisible to a status-code
        # check; they are not invisible to asking Eagle what it actually has
        # on file.
        #
        # `list_mine` is that independent read -- normalised through each
        # client's own `_normalise`, the same code path every other deadline
        # this service reports comes from. If the task the accept call just
        # reported as claimed is not in it, the claim did not land, whatever
        # the accept response itself said.
        #
        # A FAILED READ IS NOT THE SAME AS A NEGATIVE READ. The claim itself
        # may already have spent real minutes, so a network hiccup on THIS
        # follow-up read must never fail a claim that could easily be genuine
        # -- that is still reported as before, best-effort, deadline omitted.
        # It is only a READ THAT SUCCEEDS AND SHOWS THE TASK ABSENT that is
        # trusted as a real negative, because that is Eagle's own account of
        # what it holds, not a guess about why a request failed.
        try:
            mine = self._guard(source, lambda c: c.list_mine())
        except EagleIntegrationError:
            logger.warning(
                '[EAGLE] claimed %s task %s but could not read its deadline back',
                source, external_id)
            return result

        fresh = next((r for r in mine if str(r.external_id) == str(external_id)), None)
        if fresh is None:
            logger.error(
                '[EAGLE] claim for %s task %s reported success but the task '
                'does not appear in the platform\'s own list of held tasks',
                source, external_id)
            raise EagleIntegrationError(
                f'Claim for {source} task {external_id} was not confirmed: '
                f'the task does not appear in list_mine.',
                'The platform did not confirm this task was actually '
                'claimed. Please try again.',
            )
        if fresh.deadline_at:
            result['deadline_at'] = fresh.deadline_at
        return result

    def submit_task(self, external_id: str, content: str, source: str = '',
                    metadata: Optional[Dict] = None) -> EagleSubmissionResult:
        """
        Submit finished work.

        `content` is the rendered Eagle document from
        `core.services.export_formats` -- validated byte-for-byte against
        accepted submissions. It is NOT built in a browser.
        """
        product = (source or '').lower()
        logger.info('[EAGLE] submitting %s task %s (%d chars)',
                    product, external_id, len(content or ''))

        # retry_on_expiry stays False (the default) on both branches below:
        # submitting is graded work, see the module docstring and _guard's.
        # The session lock changes none of that -- it is synchronization
        # only, never a reason to retry a spend.
        if product == TRANSLATION:
            raw = self._guard(source, lambda c: c.submit(external_id, content))
            return EagleSubmissionResult(
                accepted=True, reference=str(external_id),
                message='Submitted for review.', raw=raw or {},
            )

        raw = self._guard(source, lambda c: c.submit(external_id, content))
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
                # CONFIRMED, against a real rejected task's raw response
                # (2026-09-18): Eagle's translation API names this field
                # exactly `reject_reason`, a tab-separated
                # mistake/correction/explanation table as one string. This
                # used to be discarded here -- sync.py already looked for
                # this exact key and always got an empty string, because
                # nothing forwarded it this far. See _to_task's caller in
                # sync.py for what happens when the AWARDED LIST response
                # (unlike the single-task response this was verified
                # against) turns out not to carry it per row.
                'reject_reason': str((raw.raw or {}).get('reject_reason') or ''),
                # TRANSCRIPTION'S OWN, DIFFERENT SIGNAL. CONFIRMED against
                # two real reviewed-and-failed transcription tasks
                # (2026-10-02, tasks 39321 and 39096): the translation
                # vocabulary above does not apply here at all.
                # `external_status`/`status` stays "IN_PROGRESS" forever,
                # even after a human reviewer has already failed the task
                # -- it tracks transcription PROCESSING, not the review
                # outcome. The real verdict is `cancel: 1` plus a
                # human-written reason in `cancel_reason` ("Grammar
                # errors", "Transcriber appears to have divided the
                # recording into fixed blocks rather than actual speech
                # turns"), confirmed by the matching `custom-status` label
                # Eagle shows its own users ("REVIEW FAILED"). Translation
                # rows have no `cancel` key at all, so this is harmless
                # there -- `bool(None)` is False and `cancel_reason` is
                # always ''.
                'cancel': bool((raw.raw or {}).get('cancel')),
                'cancel_reason': str((raw.raw or {}).get('cancel_reason') or ''),
            },
        )
