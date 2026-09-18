"""
Every state change goes through here. Nothing else writes Task.status.

THE LOCKING RULE: a task is re-read under `select_for_update()` inside a
transaction before any decision is made about it. Reading first and writing
after -- the pattern used elsewhere in this codebase -- is what allows two
workers to both pass a "is this free?" check and both proceed. Phase 1 found
exactly that shape guarding AI revisions, where the cost was a duplicate
Gemini bill; here the cost would be two people transcribing the same
recording, which is worse.

`select_for_update()` is a NO-OP ON SQLITE. Django accepts the call and does
nothing, so a green test run on SQLite proves nothing about concurrency.
That is why settings defaults to PostgreSQL for anything real and why the
concurrency tests assert they are running on PostgreSQL before trusting
their own result.

THE AUDIT RULE: the event is written in the SAME transaction as the state
change. Either both land or neither does, so the log can never claim
something that did not happen, nor miss something that did.
"""
import logging
from typing import Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from . import states
from .models import Task, TaskEvent, WorkerProfile

logger = logging.getLogger('workforce')


class TaskConflict(Exception):
    """
    Someone else got there first, or the task is not in a state where this
    makes sense. HTTP 409, not 400: the request was well-formed, the world
    just moved.
    """

    def __init__(self, detail: str, safe_message: Optional[str] = None):
        self.detail = detail
        self._safe = safe_message
        super().__init__(detail)

    @property
    def safe_message(self) -> str:
        return self._safe or 'That task is no longer available.'


class TaskPermissionDenied(Exception):
    """The actor may not touch this task. Deliberately does not say why."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)

    @property
    def safe_message(self) -> str:
        return 'You do not have access to that task.'


# Keys whose values must never reach the audit log. Matched as substrings
# because the shapes vary (`api_key`, `Authorization`, `set-cookie`), and a
# log that quietly captures a session cookie is a credential leak that
# outlives the session.
_REDACT_HINTS = ('password', 'token', 'secret', 'cookie', 'authorization',
                 'api_key', 'apikey', 'credential', 'session')


def _safe_metadata(metadata: Optional[dict]) -> dict:
    """Shallow-redacts anything key-shaped before it is persisted."""
    if not metadata:
        return {}
    clean = {}
    for key, value in metadata.items():
        lowered = str(key).lower()
        if any(hint in lowered for hint in _REDACT_HINTS):
            clean[key] = '***REDACTED***'
        elif isinstance(value, dict):
            clean[key] = _safe_metadata(value)
        else:
            clean[key] = value
    return clean


def record_event(
    task: Task,
    event_type: str,
    actor: Optional[User] = None,
    from_status: str = '',
    to_status: str = '',
    success: bool = True,
    message: str = '',
    metadata: Optional[dict] = None,
) -> TaskEvent:
    """
    Appends one audit row. Call inside the caller's transaction.

    Never swallows its own failure, unlike ai_budget.record_usage which
    deliberately does: a lost usage row under-counts a budget, but a lost
    audit row means the system cannot answer what happened, and that is the
    one thing this table exists for.
    """
    return TaskEvent.objects.create(
        task=task,
        actor=actor,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        success=success,
        message=message[:2000],
        metadata=_safe_metadata(metadata),
    )


def role_of(user: Optional[User]) -> str:
    if user is None:
        return states.ROLE_SYSTEM
    if user.is_staff or user.is_superuser:
        return states.ROLE_ADMIN
    return states.ROLE_WORKER


def profile_of(user: Optional[User]) -> Optional[WorkerProfile]:
    if user is None:
        return None
    return WorkerProfile.objects.filter(user=user).first()


# --------------------------------------------------------------------- #
# The single mutation primitive
# --------------------------------------------------------------------- #

def _apply_transition(
    task: Task,
    target: str,
    actor: Optional[User],
    event_type: str,
    message: str = '',
    metadata: Optional[dict] = None,
    extra_fields: Optional[dict] = None,
    success: bool = True,
) -> Task:
    """
    Moves a LOCKED task to `target`, stamps its entry timestamp, saves, and
    records the event.

    Assumes the caller already holds the row lock. Every public function
    below acquires it before calling this, which keeps the lock discipline
    in one readable place instead of repeated at each call site.
    """
    source = task.status
    states.check_transition(source, target, role_of(actor))

    task.status = target
    fields = ['status', 'updated_at']

    stamp = states.ENTRY_TIMESTAMP_FIELD.get(target)
    if stamp:
        setattr(task, stamp, timezone.now())
        fields.append(stamp)

    for name, value in (extra_fields or {}).items():
        setattr(task, name, value)
        fields.append(name)

    task.save(update_fields=list(dict.fromkeys(fields)))
    record_event(
        task, event_type, actor=actor, from_status=source, to_status=target,
        message=message, metadata=metadata, success=success,
    )
    logger.info(
        '[WORKFORCE] task=%s %s -> %s by %s', task.id, source, target,
        getattr(actor, 'username', 'system'),
    )
    _broadcast_change(task, source)
    return task


def _broadcast_change(task: Task, source: str) -> None:
    """
    Tells connected clients that this task moved.

    ON COMMIT, NOT NOW. The caller is inside `transaction.atomic()`; sending
    here would announce a state that a later failure in the same transaction
    rolls back, and clients would refetch to find the old value. Scheduling
    it on commit means the message is only ever sent for a change that
    actually happened.

    BEST EFFORT BY DESIGN. A missing or broken channel layer must not fail a
    state change -- the REST response and the audit event are the record;
    this is a nudge to refresh. Every client also polls, so a dropped
    message costs latency, not correctness.

    The payload is deliberately three fields. Anything richer would put the
    visibility rules in a second place. See consumers.py.
    """
    payload = {
        'type': 'task.changed',
        'task': str(task.id),
        'status': task.status,
        'from_status': source,
    }
    claimed_by_id = task.claimed_by_id
    assigned_to_id = task.assigned_to_id

    def send():
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            from .consumers import ADMIN_GROUP, QUEUE_GROUP, worker_group

            layer = get_channel_layer()
            if layer is None:
                return

            targets = {ADMIN_GROUP}
            # The open pool: only tasks nobody holds or is earmarked for,
            # which is exactly what visible_tasks_for grants every worker.
            in_pool_now = (task.status in states.CLAIMABLE_STATES
                           and not claimed_by_id and not assigned_to_id)
            # LEAVING THE POOL IS ALSO NEWS. Testing only the state AFTER the
            # change means a claim notifies nobody: the task is no longer
            # claimable and now has an owner, so it fails every arm of the
            # test above. Every other worker would keep the vanished task on
            # screen until their next poll and could click Claim on work that
            # is already gone -- and the same silence hides a task Eagle
            # retired because another platform worker took it.
            was_in_pool = source in states.CLAIMABLE_STATES
            if in_pool_now or was_in_pool:
                targets.add(QUEUE_GROUP)
            # The people for whom this task is personal. Read from the task
            # AFTER the change, so a release notifies the queue rather than
            # the worker who just let it go -- they already have the REST
            # response that told them it worked.
            for profile_id in (claimed_by_id, assigned_to_id):
                if profile_id:
                    targets.add(worker_group(profile_id))

            for group in targets:
                async_to_sync(layer.group_send)(group, dict(payload))
        except Exception as exc:  # pragma: no cover - transport failure
            logger.warning('[WORKFORCE] could not broadcast %s: %s', payload['task'], exc)

    transaction.on_commit(send)


def _locked(task_id) -> Task:
    """
    Re-reads the row under a write lock.

    ALWAYS re-read: the instance a view already has was fetched outside the
    transaction and its status may be stale by the time we act on it. Using
    it would reintroduce exactly the check-then-act race the lock exists to
    close.
    """
    return Task.objects.select_for_update().get(pk=task_id)


# --------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------- #

def create_task(actor: Optional[User] = None, **fields) -> Task:
    with transaction.atomic():
        task = Task.objects.create(**fields)
        record_event(
            task, TaskEvent.TASK_CREATED, actor=actor, to_status=task.status,
            metadata={'kind': task.kind, 'external_task_id': task.external_task_id},
        )
    return task


def assign_task(task_id, worker: WorkerProfile, actor: User) -> Task:
    """Administrative earmark. Does not make the worker the holder."""
    with transaction.atomic():
        task = _locked(task_id)
        if task.claimed_by_id and task.claimed_by_id != worker.id:
            raise TaskConflict(
                f'Task {task.id} is held by {task.claimed_by_id}.',
                'That task is currently being worked on by someone else.',
            )
        if not worker.is_active_worker:
            raise TaskConflict(
                f'Worker {worker.worker_code} is not active.',
                'That worker is not currently active.',
            )
        return _apply_transition(
            task, states.ASSIGNED, actor, TaskEvent.TASK_ASSIGNED,
            metadata={'worker_code': worker.worker_code},
            extra_fields={'assigned_to': worker},
        )


def unassign_task(task_id, actor: User) -> Task:
    with transaction.atomic():
        task = _locked(task_id)
        return _apply_transition(
            task, states.NEW, actor, TaskEvent.TASK_UNASSIGNED,
            extra_fields={'assigned_to': None},
        )


def claim_task(task_id, worker: WorkerProfile, actor: User, deadline_at=None) -> Task:
    """
    THE OPERATION THAT MUST NOT RACE.

    Two workers hitting this simultaneously: the first to acquire the row
    lock proceeds; the second blocks until that transaction commits, then
    re-reads and finds `claimed_by` populated, and is refused. Neither the
    ordering nor the outcome depends on anything the frontend does.
    """
    with transaction.atomic():
        task = _locked(task_id)

        # NOT is not None -- is held by someone ELSE. eagle_claim.py's
        # reservation step sets claimed_by=worker on THIS SAME worker
        # before calling here to finalize, specifically to close the
        # cross-worker race against Eagle's own claim call. Rejecting any
        # non-null claimed_by, the original check, would reject a
        # worker's own reservation finalizing -- exactly the case this
        # exists to let through.
        if task.claimed_by_id is not None and task.claimed_by_id != worker.id:
            holder = task.claimed_by_id
            raise TaskConflict(
                f'Task {task.id} already claimed by worker {holder}.',
                'That task has already been claimed by another worker.',
            )
        if task.status not in states.CLAIMABLE_STATES:
            raise TaskConflict(
                f'Task {task.id} is in state {task.status}.',
                'That task is not available to claim.',
            )
        if not worker.is_active_worker:
            raise TaskConflict(
                f'Worker {worker.worker_code} is not active.',
                'Your account is not active for task work.',
            )
        # An assignment to someone else is a reservation, and a worker
        # cannot claim past it.
        if task.assigned_to_id and task.assigned_to_id != worker.id:
            raise TaskPermissionDenied(
                f'Task {task.id} is assigned to worker {task.assigned_to_id}.'
            )

        held = Task.objects.filter(
            claimed_by=worker, status__in=states.WORKER_HELD_STATES,
        ).count()
        if held >= worker.max_concurrent_tasks:
            raise TaskConflict(
                f'Worker {worker.worker_code} holds {held} tasks.',
                f'You already hold {held} tasks. Finish or release one first.',
            )

        extra_fields = {'claimed_by': worker, 'claim_reserved_at': None}
        # The one-hour countdown a worker actually sees starts here, if the
        # caller could read it back from Eagle -- see eagle_claim.py and
        # EagleIntegrationService.claim_task. Left untouched when absent
        # rather than cleared, so a value the periodic sync already found
        # (unlikely this early, but not impossible) is never thrown away.
        if deadline_at is not None:
            extra_fields['deadline_at'] = deadline_at

        # CLEAR ANY WORKFORCE_TRAINING_MODE RESIDUE, BUT ONLY ON THIS
        # TASK'S FIRST-EVER REAL CLAIM.
        #
        # Training mode (see its docstring in settings.py) lets a tasker
        # save a polish/accuracy-check onto a task they have not claimed,
        # specifically so onboarding does not have to spend real Eagle
        # minutes. That leaves real-looking data sitting on a real,
        # Eagle-synced task row -- and a genuine worker who claims it for
        # real later must never see somebody else's rehearsal sitting in
        # the box they are about to submit from.
        #
        # "First-ever real claim" is the exact condition: no prior
        # TASK_CLAIMED event means the only way polished_text or
        # accuracy_check_notes could be non-empty is the training-mode
        # bypass (or a deliberate admin write) -- never a previous real
        # holder, because there was none. A release-and-reclaim, by
        # contrast, already has a TASK_CLAIMED event on record and is left
        # completely alone here: that data belongs to this task's real
        # work history, not a rehearsal, and clearing it would destroy a
        # legitimate prior worker's saved progress.
        first_real_claim = not TaskEvent.objects.filter(
            task=task, event_type=TaskEvent.TASK_CLAIMED,
        ).exists()
        if first_real_claim and (task.polished_text or task.accuracy_check_notes):
            extra_fields.update({
                'polished_text': '', 'polished_by': None, 'polished_at': None,
                'accuracy_check_notes': '', 'accuracy_check_by': None,
                'accuracy_check_at': None,
            })

        return _apply_transition(
            task, states.CLAIMED, actor, TaskEvent.TASK_CLAIMED,
            metadata={'worker_code': worker.worker_code},
            # claim_reserved_at=None whether or not a reservation actually
            # preceded this call -- a task claimed without going through
            # eagle_claim.py's reservation step (a local task, or a direct
            # call) never had it set in the first place, so clearing it is
            # a no-op there and the real cleanup for the reserved case.
            extra_fields=extra_fields,
        )


def release_task(task_id, actor: User, reason: str = '') -> Task:
    """Hands a task back. Returns to the worker it was assigned to if there
    was one, otherwise to the open pool."""
    with transaction.atomic():
        task = _locked(task_id)
        target = states.NEW
        return _apply_transition(
            task, target, actor, TaskEvent.TASK_RELEASED,
            message=reason,
            extra_fields={'claimed_by': None},
        )


def start_task(task_id, actor: Optional[User]) -> Task:
    with transaction.atomic():
        task = _locked(task_id)
        return _apply_transition(
            task, states.IN_PROGRESS, actor, TaskEvent.TASK_STARTED,
        )


def begin_review(task_id, actor: Optional[User]) -> Task:
    with transaction.atomic():
        task = _locked(task_id)
        return _apply_transition(
            task, states.REVIEW, actor, TaskEvent.REVIEW_STARTED,
        )


def mark_ready(task_id, actor: User, revision=None) -> Task:
    with transaction.atomic():
        task = _locked(task_id)
        extra = {'deliverable_revision': revision} if revision is not None else None
        return _apply_transition(
            task, states.READY_TO_SUBMIT, actor, TaskEvent.READY_FOR_SUBMISSION,
            extra_fields=extra,
        )


def mark_submitted(
    task_id, actor: Optional[User], reference: str = '', content: str = '',
) -> Task:
    """
    Records that submission happened. Does NOT perform it -- Phase 4's
    integration service does, and calls this once the external platform has
    acknowledged.

    `content`, when given, is stored as `submitted_content` -- the frozen
    record of exactly what Eagle received, since a rejection can land on
    this task long after `job`/`polished_text` have moved on. See Task's
    own docstring for why this is the one document body the model keeps.
    """
    with transaction.atomic():
        task = _locked(task_id)
        return _apply_transition(
            task, states.SUBMITTED, actor, TaskEvent.SUBMISSION_COMPLETED,
            metadata={'reference': reference} if reference else None,
            extra_fields={'submitted_content': content} if content else None,
        )


def mark_approved(task_id, actor: Optional[User], note: str = '') -> Task:
    with transaction.atomic():
        task = _locked(task_id)
        return _apply_transition(
            task, states.APPROVED, actor, TaskEvent.APPROVED, message=note,
        )


def mark_rejected(task_id, actor: Optional[User], reason: str) -> Task:
    with transaction.atomic():
        task = _locked(task_id)
        return _apply_transition(
            task, states.REJECTED, actor, TaskEvent.REJECTED,
            message=reason, extra_fields={'rejection_reason': reason},
        )


def mark_failed(task_id, actor: Optional[User], reason: str) -> Task:
    """
    Failure preserves diagnostics: the reason lands on the task AND in the
    event, so a later retry does not erase why the first attempt died.
    """
    with transaction.atomic():
        task = _locked(task_id)
        return _apply_transition(
            task, states.FAILED, actor, TaskEvent.TASK_FAILED,
            message=reason, success=False,
            extra_fields={'failure_reason': reason},
        )


def cancel_task(task_id, actor: User, reason: str = '') -> Task:
    with transaction.atomic():
        task = _locked(task_id)
        return _apply_transition(
            task, states.CANCELLED, actor, TaskEvent.TASK_CANCELLED,
            message=reason, extra_fields={'claimed_by': None},
        )


def transition(task_id, target: str, actor: Optional[User], message: str = '') -> Task:
    """
    Generic escape hatch for transitions without a dedicated helper.

    Still goes through the machine and still records an event -- it is a
    convenience, not a bypass. There is deliberately no function anywhere
    that sets `status` without consulting `states.check_transition`.
    """
    with transaction.atomic():
        task = _locked(task_id)
        return _apply_transition(
            task, target, actor, TaskEvent.STATUS_CHANGED, message=message,
        )


def retire_task(task_id, reason: str = '') -> Task:
    """
    Retire a task that the external platform no longer offers.

    A SYSTEM act, performed by the Eagle sync when a task disappears from
    the available list -- which on a shared platform means another worker
    took it. Goes through `_apply_transition` like every other state change,
    so it is locked, legal and audited rather than a direct write.
    """
    with transaction.atomic():
        task = _locked(task_id)
        return _apply_transition(
            task, states.CANCELLED, None, TaskEvent.TASK_CANCELLED,
            message=reason or 'No longer offered on the external platform.',
            metadata={'reason': 'taken_by_another_worker'},
        )
