"""
Claiming a task on Eagle, on a worker's behalf.

THIS IS THE FILE THAT SPENDS MONEY. A claim deducts purchased minutes and
starts a one-hour deadline. Everything here is arranged around two facts that
follow from that:

  * A claim we cannot afford must be refused BEFORE it is attempted, because
    a failed claim can still burn a session round-trip and, worse, a partial
    one leaves a task in an unknown state.
  * A claim we cannot USE must not happen either. Claiming a task nobody is
    about to work is how minutes turn into expired tasks.

ORDER MATTERS. Eagle first, then our own state. If we marked the task claimed
locally and Eagle then refused, a worker would sit editing a task they do not
hold and submit into nothing. So the external call happens first, and our
transition only follows an acknowledgement. The reverse -- Eagle accepted but
our commit failed -- is recoverable by the sync; the other direction is not.

THE MINUTES FLOOR IS DYNAMIC. Not "refuse below 25 minutes": a 3-minute task
is perfectly claimable on a 5-minute balance, and a flat floor would idle a
team over an arithmetic detail. What matters is whether THIS task fits.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from django.db import transaction

from . import services, states
from .eagle import EagleAuthError, EagleIntegrationError, EagleNotConfigured
from .models import Task, TaskEvent, WorkerProfile

logger = logging.getLogger('eagle')


class ClaimRefused(Exception):
    """
    A claim that must not proceed. Carries the split used throughout: full
    reason for logs, a sentence a worker can act on for the response.
    """

    def __init__(self, detail: str, safe_message: str = ''):
        self.detail = detail
        self._safe = safe_message
        super().__init__(detail)

    @property
    def safe_message(self) -> str:
        return self._safe or 'That task could not be started right now.'


class InsufficientMinutes(ClaimRefused):
    """
    The account cannot afford this task.

    An OPERATOR problem, not a worker's. The safe message says so plainly
    rather than implying the worker did something wrong, and the operator is
    alerted separately.
    """


@dataclass
class MinutesCheck:
    required: int
    available: Optional[int]
    sufficient: bool
    reason: str = ''


def minutes_required_for(task: Task) -> int:
    """
    What this task will cost, in whole minutes.

    Rounded UP: platforms bill whole minutes, and a 10.3-minute file that we
    treat as 10 when it is charged as 11 produces a claim that fails at the
    worst possible moment -- after a worker has been told to start.
    """
    # THE PLATFORM'S OWN NUMBER WINS. It is the one enforcing the charge,
    # and it does not always agree with the arithmetic: a 5m04s file is 6
    # minutes rounded up and Eagle asks for 5. Preferring our estimate there
    # refuses a claim the platform would have accepted, which costs a task
    # for no reason at all.
    stated = getattr(task, 'minutes_required', None)
    if stated:
        return int(stated)

    if not task.duration_seconds:
        return 0
    minutes = task.duration_seconds / 60.0
    return int(minutes) if minutes == int(minutes) else int(minutes) + 1


def check_minutes(task: Task, service=None) -> MinutesCheck:
    """
    Can the account afford this specific task right now?

    An unknown balance is treated as sufficient rather than blocking: some
    products do not report one, and refusing all work because a number is
    absent would be worse than letting the platform itself decline.
    """
    from .eagle import get_eagle_service

    service = service or get_eagle_service()
    required = minutes_required_for(task)

    available = None
    if hasattr(service, 'minutes_balance'):
        available = service.minutes_balance(task.external_source)

    if available is None:
        return MinutesCheck(required, None, True, 'balance unknown')
    if available >= required:
        return MinutesCheck(required, available, True)
    return MinutesCheck(
        required, available, False,
        f'needs {required} minutes, {available} available',
    )


def claim_for_worker(task_id, worker: WorkerProfile, actor, service=None) -> Task:
    """
    Claim on Eagle, then record it here.

    Refuses early and cheaply for anything knowable in advance: an
    unaffordable task, a task already held, a worker at their limit. The
    external call is the last thing to happen before the transition.
    """
    from .eagle import get_eagle_service

    service = service or get_eagle_service()
    task = Task.objects.select_related('claimed_by').get(pk=task_id)

    if task.status not in states.CLAIMABLE_STATES or task.claimed_by_id:
        raise ClaimRefused(
            f'Task {task.id} is {task.status} and held by {task.claimed_by_id}.',
            'Someone else already started that task.',
        )

    held = Task.objects.filter(
        claimed_by=worker, status__in=states.WORKER_HELD_STATES,
    ).count()
    if held >= worker.max_concurrent_tasks:
        raise ClaimRefused(
            f'{worker.worker_code} holds {held} tasks.',
            f'You already have {held} tasks open. Finish or release one first.',
        )

    if not task.external_task_id:
        # A locally created task has nothing to claim externally; it is ours
        # already. Fall straight through to the normal service path.
        return services.claim_task(task.id, worker=worker, actor=actor)

    check = check_minutes(task, service=service)
    if not check.sufficient:
        logger.error('[CLAIM] insufficient minutes for %s: %s',
                     task.external_task_id, check.reason)
        _record_refusal(task, actor, check)
        raise InsufficientMinutes(
            f'Task {task.external_task_id} needs {check.required} minutes, '
            f'{check.available} available.',
            'There are not enough platform minutes to start this task. '
            'An administrator has been notified.',
        )

    # --- the external call. Everything above was free; this is not. ---- #
    try:
        result = service.claim_task(task.external_task_id, task.external_source)
    except (EagleNotConfigured, EagleAuthError) as exc:
        raise ClaimRefused(exc.detail, exc.safe_message) from exc
    except EagleIntegrationError as exc:
        raise ClaimRefused(
            exc.detail,
            'The task platform could not be reached. Please try again.',
        ) from exc

    if isinstance(result, dict) and result.get('accepted') is False:
        raise ClaimRefused(
            f'Platform refused the claim for {task.external_task_id}.',
            'That task is no longer available.',
        )

    with transaction.atomic():
        claimed = services.claim_task(task.id, worker=worker, actor=actor)
        services.record_event(
            claimed, TaskEvent.STATUS_CHANGED, actor=actor,
            from_status=states.NEW, to_status=states.CLAIMED,
            message='Claimed on the external platform.',
            metadata={
                'external_task_id': task.external_task_id,
                'minutes_spent': check.required,
                'minutes_remaining': (result or {}).get('minutes_balance'),
            },
        )
    logger.info('[CLAIM] %s claimed %s task %s (%d minutes)',
                worker.worker_code, task.external_source,
                task.external_task_id, check.required)
    return claimed


def _record_refusal(task: Task, actor, check: MinutesCheck) -> None:
    """
    Leave a trace when work is refused for want of minutes.

    Audited because it is an operational fact the operator needs -- an
    afternoon of these is the signal to top up, and it should be visible
    without reading logs.
    """
    try:
        services.record_event(
            task, TaskEvent.STATUS_CHANGED, actor=actor,
            from_status=task.status, to_status=task.status, success=False,
            message='Claim refused: not enough platform minutes.',
            metadata={'minutes_required': check.required,
                      'minutes_available': check.available},
        )
    except Exception:
        # Never let the audit write mask the real refusal.
        logger.exception('[CLAIM] could not record a minutes refusal')
