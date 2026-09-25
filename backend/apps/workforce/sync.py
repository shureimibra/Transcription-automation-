"""
Bringing Eagle's tasks into our Task table.

WHAT THIS IS AND IS NOT. It is a read: it discovers what Eagle is offering
and mirrors it so workers see a queue without ever touching Eagle. It claims
nothing, submits nothing and spends no minutes. Claiming is a separate,
deliberate act in services.py, triggered by a worker asking for work.

IDEMPOTENT BY CONSTRUCTION. Phase 2 put a partial unique index on
`(external_source, external_task_id)`, so re-running this cannot duplicate a
task. That matters more than it sounds: the alternative -- read, check, then
write -- is a race that two poll cycles can lose to each other, and the
symptom would be the same task offered to two workers.

THE POLL IS ALSO THE KEEP-ALIVE. Laravel idles a session out after about two
hours. Running every 30-60 seconds means we are never idle for two minutes,
so no separate heartbeat is needed and the session survives the working day
as a side effect of doing the job.

WHAT IT DOES NOT DECIDE. Whether a task is worth doing, who gets it, or when
to claim it. Those belong to the operator and the worker respectively.
"""
import datetime as dt
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import services, states
from .credentials import TRANSCRIPTION, TRANSLATION
from .eagle import EagleAuthError, EagleIntegrationError, EagleNotConfigured
from .models import Task

logger = logging.getLogger('eagle')


@dataclass
class SyncReport:
    """What one cycle did. Returned rather than logged-and-forgotten so the
    admin dashboard can show it and tests can assert on it."""
    product: str = ''
    seen: int = 0
    created: int = 0
    updated: int = 0
    taken_by_others: int = 0
    reviewed: int = 0
    undelivered: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)
    session_ok: bool = True
    minutes_balance: Optional[int] = None

    def as_dict(self) -> Dict:
        return {
            'product': self.product, 'seen': self.seen, 'created': self.created,
            'updated': self.updated, 'taken_by_others': self.taken_by_others,
            'reviewed': self.reviewed, 'undelivered': self.undelivered,
            'skipped': self.skipped, 'errors': self.errors,
            'session_ok': self.session_ok, 'minutes_balance': self.minutes_balance,
        }


def _kind_for(external_task) -> str:
    return (Task.TRANSLATION if external_task.kind == 'translation'
            else Task.TRANSCRIPTION)


def sync_product(product: str, service=None) -> SyncReport:
    """
    One poll cycle for one Eagle product.

    Never raises for an expected condition. An expired session or an
    unconfigured credential is reported in the SyncReport, because a poller
    that dies on a stale cookie takes the whole queue down with it -- and
    the tasks already claimed by workers still need serving.
    """
    from .eagle import get_eagle_service

    service = service or get_eagle_service()
    report = SyncReport(product=product)

    try:
        health = service.check_session(product)
        report.minutes_balance = health.get('minutes_balance')
    except (EagleNotConfigured, EagleAuthError) as exc:
        report.session_ok = False
        report.errors.append(exc.safe_message)
        logger.warning('[SYNC] %s session unusable: %s', product, exc.safe_message)
        return report
    except EagleIntegrationError as exc:
        report.session_ok = False
        report.errors.append(exc.safe_message)
        return report

    try:
        available = service.list_available_tasks(product)
    except EagleIntegrationError as exc:
        report.errors.append(exc.safe_message)
        return report

    report.seen = len(available)
    seen_ids = set()

    for external in available:
        seen_ids.add(str(external.external_id))
        try:
            created = _upsert(external, product)
            if created:
                report.created += 1
            else:
                report.updated += 1
        except Exception as exc:  # one bad row must not stop the cycle
            report.skipped += 1
            report.errors.append(f'{external.external_id}: {type(exc).__name__}')
            logger.exception('[SYNC] could not mirror %s task %s',
                             product, external.external_id)

    report.taken_by_others = _retire_vanished(product, seen_ids)
    report.reviewed = _reconcile_outcomes(product, service, report)
    return report


#: Eagle's own words for "the review is done", and what each means for us.
#:
#: DELIBERATELY ABSENT: `submitted` and `IN_PROGRESS`. Both mean the review
#: has NOT happened -- the transcription product shows IN_PROGRESS in its
#: awarded list beside the label "Pending", which is the same state a task is
#: already in here. Note that IN_PROGRESS means something different in the
#: AVAILABLE list, where it maps to `available`; the word is overloaded and
#: only the feed it came from disambiguates it.
#:
#: WHAT A REVIEWED TRANSCRIPTION LOOKS LIKE IS STILL UNKNOWN. No transcription
#: task has been reviewed yet, so its accepted/rejected vocabulary has never
#: been observed. `_note_unknown_outcome` below logs anything unrecognised so
#: the first review teaches us the word rather than passing silently.
_OUTCOMES = {
    'completed': states.APPROVED,
    'approved': states.APPROVED,
    'reviewed': states.APPROVED,
    'rejected': states.REJECTED,
    'review_failed': states.REJECTED,
    'failed': states.REJECTED,
}


#: Statuses already reported, so the log below fires once per word rather
#: than once per task per cycle.
_UNKNOWN_OUTCOMES = set()

#: Words we know mean "still waiting", so they are not worth reporting.
_PENDING_WORDS = {'submitted', 'in_progress', 'pending', 'processing', 'awarded', ''}


def _note_unknown_outcome(product: str, external_status: str) -> None:
    """
    Say so, once, when the platform uses a word we do not recognise.

    The transcription product has never had a task reviewed, so we have
    never seen what it calls "accepted" or "failed". Rather than guess a
    vocabulary, unknown words pass through as "still pending" -- the safe
    reading -- and announce themselves here so the first real review is a
    log line to act on instead of an outcome that silently never arrives.
    """
    word = (external_status or '').strip().lower()
    if word in _PENDING_WORDS:
        return
    key = (product, word)
    if key in _UNKNOWN_OUTCOMES:
        return
    _UNKNOWN_OUTCOMES.add(key)
    logger.warning(
        '[SYNC] %s awarded feed used an unrecognised status %r. Treating it as '
        'still pending. If this is a review outcome, add it to _OUTCOMES.',
        product, external_status,
    )

def _format_reject_reason(raw: str) -> str:
    """
    Eagle's translation rejection reason arrives as one string holding a
    tab-separated table: a header row (`Mistake / Issue`, `Correction /
    Suggested version`, `Explanation`), then one row per item as `number
    \\t issue \\t correction \\t explanation`. Confirmed against a real
    rejected task's raw API response on 2026-09-18 -- this is not a guess.

    Shown raw, the tabs collapse to single spaces in HTML and the whole
    thing reads as one run-on sentence. This turns it back into the numbered
    list it actually is.

    TOLERANT OF ANYTHING ELSE. A plain-text reason (or a rejection from the
    transcription product, whose real shape has never been confirmed) has
    no tabs at all and is returned unchanged -- this only ever reformats
    the one shape it has actually seen, never guesses at another.
    """
    text = (raw or '').strip()
    if '\t' not in text:
        return text

    lines = text.split('\n')
    blocks = []
    for line in lines:
        fields = line.split('\t')
        # A genuine item row is "number, issue, correction, explanation".
        # The header row (3 fields, no leading number) and anything else
        # unrecognised is skipped rather than mangled into a fake item.
        if len(fields) < 3 or not fields[0].strip().isdigit():
            continue
        n = fields[0].strip()
        issue = fields[1].strip() if len(fields) > 1 else ''
        correction = fields[2].strip() if len(fields) > 2 else ''
        explanation = fields[3].strip() if len(fields) > 3 else ''
        block = f'{n}. {issue}'
        if correction:
            block += f'\n   -> {correction}'
        if explanation:
            block += f'\n   {explanation}'
        blocks.append(block)

    # Nothing recognisable as a row: safer to hand back the original text
    # than to return an empty string and lose the reason entirely.
    return '\n\n'.join(blocks) if blocks else text


def _reconcile_outcomes(product: str, service, report) -> int:
    """
    Bring back the platform's verdict on work we already sent.

    WHY THIS EXISTS. The sync only ever read the AVAILABLE list, so the
    moment a task was submitted it fell out of view and its status froze at
    `submitted` for ever. Eagle knows perfectly well whether the work was
    accepted -- its awarded feed carries `completed`, `rejected` and the
    reject reason, and the money lands against it -- and none of that was
    reaching either the worker who did the job or the person deciding what
    to pay them.

    A worker cannot improve without knowing which submissions came back, and
    an operator cannot judge a tasker on anything but volume. This is the
    feed that makes both possible.

    NEVER MOVES A TASK A WORKER IS STILL HOLDING. Only `submitted` tasks are
    reconciled -- an outcome is news about finished work, and a poll must
    not reach into anything in progress.
    """
    lister = getattr(service, 'list_my_tasks', None)
    if lister is None:
        return 0
    try:
        mine = lister(product)
    except EagleIntegrationError as exc:
        # Not fatal: outcomes are news, and news can wait for the next turn.
        # The feed did not answer. Absence proves nothing, so the
        # undelivered check is skipped entirely rather than run on a blank.
        report.errors.append(f'outcomes: {exc.safe_message}')
        return 0
    except Exception as exc:
        report.errors.append(f'outcomes: {type(exc).__name__}')
        return 0

    changed = 0
    seen_ids = set()
    for external in mine or []:
        seen_ids.add(str(external.external_id))
        raw = external.raw or {}
        external_status = str(raw.get('external_status') or '').strip()
        verdict = _OUTCOMES.get(external_status.lower())
        if verdict is None:
            _note_unknown_outcome(product, external_status)
            continue

        task = Task.objects.filter(
            external_source=product,
            external_task_id=str(external.external_id),
            status=states.SUBMITTED,
        ).first()
        if task is None:
            continue

        reason = str(raw.get('reject_reason') or '').strip()
        # THE BULK AWARDED LIST MAY NOT CARRY THE REASON PER ROW -- only
        # confirmed, against a real task, for the single-task detail
        # response (see eagle_impl.py::_to_task). If the list gave us
        # nothing for a rejection, ask for that one task directly rather
        # than settle for the generic fallback while a real reason exists
        # one call away.
        if verdict != states.APPROVED and not reason:
            getter = getattr(service, 'get_task', None)
            if getter is not None:
                try:
                    detail = getter(external.external_id, product)
                    reason = str((detail.raw or {}).get('reject_reason') or '').strip()
                except Exception as exc:
                    logger.warning(
                        '[SYNC] could not fetch reject reason for %s task %s: %s',
                        product, external.external_id, exc,
                    )
        reason = _format_reject_reason(reason)
        try:
            if verdict == states.APPROVED:
                services.mark_approved(task.id, actor=None,
                                       note=f'Accepted by the platform. {reason}'.strip())
            else:
                services.mark_rejected(
                    task.id, actor=None,
                    reason=reason or 'The platform did not accept this submission.')
            changed += 1
            logger.info('[SYNC] %s task %s came back %s', product,
                        external.external_id, verdict)
        except Exception as exc:
            report.errors.append(f'{external.external_id}: {type(exc).__name__}')

    report.undelivered = _flag_undelivered(product, seen_ids, report)
    return changed


#: How long after its deadline a submission may still be missing from the
#: awarded feed before we call it undelivered. Generous on purpose: the cost
#: of waiting is a row that says "awaiting review" for another few hours; the
#: cost of being wrong is telling somebody their finished work was lost.
UNDELIVERED_GRACE_HOURS = int(os.environ.get('EAGLE_UNDELIVERED_GRACE_HOURS', '6'))


def _flag_undelivered(product: str, seen_ids: set, report) -> int:
    """
    Work we believe we sent that the platform has no record of.

    WHY ABSENCE IS THE SIGNAL. `_reconcile_outcomes` only ever acts on rows
    the awarded feed RETURNS, so it answers "the platform decided" and has
    no answer at all for "the platform never heard of this". A submission
    that failed to land therefore sits at `submitted` for ever: it shows as
    "Awaiting review" on the tasker's screen and counts as delivered volume
    when somebody decides awards.

    Four real tasks were in exactly that state -- three claimed cold this
    morning and marked submitted through the record-only button after their
    deadlines lapsed, plus one that had been waiting six days. Eagle's
    transcription feed listed one task; ours listed five.

    THE SAFETY IS IN THE PRECONDITIONS, because the failure mode here is
    telling a worker that good work was lost:

      * the feed must have been fetched SUCCESSFULLY -- absence from a
        listing we could not read means nothing, so the caller passes None
        and we do nothing at all
      * the deadline must be past, plus a grace period, so work still
        legitimately in flight is never touched
      * a task the feed DID return is never considered, whatever it says

    Note the feed carries history -- a rejection from days earlier is still
    listed -- so absence is a genuine signal rather than a short window.
    """
    if seen_ids is None:
        return 0

    cutoff = timezone.now() - dt.timedelta(hours=UNDELIVERED_GRACE_HOURS)
    stale = Task.objects.filter(
        external_source=product, status=states.SUBMITTED,
    ).exclude(external_task_id__in=list(seen_ids))

    count = 0
    for task in stale:
        # The deadline is the honest clock where we have one; without it,
        # fall back to how long ago we sent it.
        reference = task.deadline_at or task.submitted_at
        if reference is None or reference > cutoff:
            continue
        try:
            services.mark_failed(
                task.id, actor=None,
                reason=('This was recorded as submitted here, but the platform '
                        'has no record of receiving it and its deadline has '
                        'passed. It was not delivered.'),
            )
            count += 1
            logger.warning('[SYNC] %s task %s was never received by the platform',
                           product, task.external_task_id)
        except states.IllegalTransition:
            continue
        except Exception as exc:
            report.errors.append(f'{task.external_task_id}: {type(exc).__name__}')
    return count


def _transcription_local_zone():
    """The zone Eagle's transcription site writes its bare timestamps in.
    A fixed offset, not a named zone: it needs no tz database, and the
    platform has been observed to keep one offset year-round."""
    from django.conf import settings
    hours = getattr(settings, 'EAGLE_TRANSCRIPTION_UTC_OFFSET_HOURS', 3)
    return dt.timezone(dt.timedelta(hours=float(hours)))


def _parse_deadline(value, product=''):
    """
    Eagle's deadline as a datetime, or None for anything unusable.

    A TIMESTAMP WITH NO ZONE IS NOT UTC. Translation sends
    "2026-09-24T00:00:00Z", which parses unambiguously. Transcription sends
    "2026-09-25 17:32:56" -- Eagle's own local time, three hours ahead of
    UTC. Reading that as UTC put every transcription deadline exactly three
    hours late (240 minutes on the worker's clock instead of 60), while
    translation, which carries its zone, was correct.
    """
    text = str(value or '').strip()
    if not text or text.lower() in ('none', 'null', '-'):
        return None
    parsed = parse_datetime(text)
    if parsed is None:
        return None
    if timezone.is_aware(parsed):
        return parsed
    if product == TRANSCRIPTION:
        return timezone.make_aware(parsed, _transcription_local_zone())
    return timezone.make_aware(parsed)

def _upsert(external, product: str) -> bool:
    """
    Mirror one external task. Returns True when newly created.

    Only ever writes the DESCRIPTIVE fields. It never touches `status`,
    `claimed_by` or any timestamp, because those belong to our state machine
    -- a poll must not be able to move a task a worker is holding.
    """
    raw = external.raw or {}
    defaults = {
        'kind': _kind_for(external),
        'title': external.title or f'Task {external.external_id}',
        # The label that decides the transcription standard. Mirrored like
        # any other descriptive field -- the platform owns it, we read it.
        'project': external.project or '',
        'source_url': external.page_url or '',
        'source_language': external.source_language or '',
        'target_language': external.target_language or '',
        'duration_seconds': external.duration_seconds,
        # The platform's own billing figure, which beats our estimate.
        'minutes_required': external.minutes_required,
        # PARSED HERE, NOT TRUSTED RAW. Eagle sends 'None' as a literal
        # string when it has not set a deadline yet, and a DateTimeField
        # handed that raises rather than storing null.
        'deadline_at': _parse_deadline(external.deadline_at, product),
    }

    with transaction.atomic():
        task = Task.objects.filter(
            external_source=product, external_task_id=str(external.external_id),
        ).first()

        if task is not None:
            changed = [f for f, v in defaults.items() if getattr(task, f) != v and v]
            for field_name in changed:
                setattr(task, field_name, defaults[field_name])
            if changed:
                task.save(update_fields=changed + ['updated_at'])
            return False

        try:
            # Through the service layer, so creation is audited the same way
            # every other task is -- not a bare ORM insert with a hand-rolled
            # event beside it.
            services.create_task(
                actor=None,
                external_source=product,
                external_task_id=str(external.external_id),
                **defaults,
            )
        except IntegrityError:
            # The partial unique index caught a concurrent cycle. Correct
            # behaviour, not an error: the other cycle created it.
            return False
        return True


def _retire_vanished(product: str, seen_ids) -> int:
    """
    A task that has left Eagle's available list while still NEW here was
    taken by someone else -- Eagle's pool is shared with every other worker
    on the platform.

    Cancelled rather than deleted: the row is why we pre-processed audio for
    it, and losing races is a rate worth being able to measure. Only NEW
    tasks are touched, so anything a worker holds is never disturbed.
    """
    # An empty `seen_ids` needs no sentinel: `exclude(field__in=[])` excludes
    # nothing, so every NEW task for this product is stale -- which is
    # exactly right when the platform is offering none.
    stale = Task.objects.filter(
        external_source=product, status=states.NEW, claimed_by__isnull=True,
    ).exclude(external_task_id__in=list(seen_ids))

    count = 0
    for task in stale:
        if not task.external_task_id:
            continue
        try:
            # THROUGH THE SERVICE LAYER. Writing `task.status` here would be
            # the one thing services.py exists to prevent: unlocked,
            # unaudited, and able to move a task the machine says cannot
            # move. `retire_task` is a SYSTEM transition, and the state
            # table was extended to permit it rather than bypassed.
            services.retire_task(task.id)
            count += 1
        except states.IllegalTransition:
            # It moved between the query and here -- a worker claimed it in
            # the same instant. Their claim wins; nothing to retire.
            continue
    return count


def sync_all(service=None) -> List[SyncReport]:
    """Both products. Order is not significant; failures are independent."""
    return [sync_product(p, service=service) for p in (TRANSCRIPTION, TRANSLATION)]
