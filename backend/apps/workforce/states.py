"""
The task lifecycle, as data.

WHY A TABLE AND NOT `if` STATEMENTS: a state machine scattered across view
methods is one forgotten branch away from letting a rejected task be
submitted again, or a worker moving someone else's task to APPROVED. Here
the legal moves are a single dict that can be read in one screen, asserted
against in tests, and rendered in the admin UI without re-deriving anything.

WHO may perform a transition is part of the machine, not a separate concern
layered on top. Approval is an administrative act; claiming is a worker act;
failure is a system act. Encoding the actor role alongside the transition is
what stops "worker can PATCH status=APPROVED" from ever being reachable,
regardless of which endpoint is added later.
"""
from typing import Dict, FrozenSet, Optional, Set, Tuple

# --------------------------------------------------------------------- #
# States
# --------------------------------------------------------------------- #

NEW = 'new'
ASSIGNED = 'assigned'
CLAIMED = 'claimed'
IN_PROGRESS = 'in_progress'
REVIEW = 'review'
READY_TO_SUBMIT = 'ready_to_submit'
SUBMITTED = 'submitted'
APPROVED = 'approved'
REJECTED = 'rejected'
FAILED = 'failed'
CANCELLED = 'cancelled'

STATUS_CHOICES = [
    (NEW, 'New'),
    (ASSIGNED, 'Assigned'),
    (CLAIMED, 'Claimed'),
    (IN_PROGRESS, 'In progress'),
    (REVIEW, 'Review'),
    (READY_TO_SUBMIT, 'Ready to submit'),
    (SUBMITTED, 'Submitted'),
    (APPROVED, 'Approved'),
    (REJECTED, 'Rejected'),
    (FAILED, 'Failed'),
    (CANCELLED, 'Cancelled'),
]

ALL_STATES: FrozenSet[str] = frozenset(code for code, _ in STATUS_CHOICES)

# Nothing leaves these. A task here is finished, and the only way to work on
# it again is a new task -- which keeps the audit trail of the first attempt
# intact instead of overwriting it.
#
# REJECTED IS TERMINAL, NOT A DETOUR BACK TO WORK. The external platform's
# review decision is final and cannot be appealed or redone -- a rejection
# is a permanent loss of the task, not a note to act on and resubmit. This
# used to be modelled the other way (REJECTED -> IN_PROGRESS was a legal
# transition, and the worker dashboard offered "Start work" on a rejected
# task as if it were revisable) on the assumption that Eagle's rejections
# could be corrected and resent. They cannot.
TERMINAL_STATES: FrozenSet[str] = frozenset({APPROVED, REJECTED, CANCELLED})

# States in which a worker holds the task and nobody else may take it.
WORKER_HELD_STATES: FrozenSet[str] = frozenset({
    CLAIMED, IN_PROGRESS, REVIEW, READY_TO_SUBMIT,
})

# States a task can be claimed from.
CLAIMABLE_STATES: FrozenSet[str] = frozenset({NEW, ASSIGNED})


# --------------------------------------------------------------------- #
# Actors
# --------------------------------------------------------------------- #

ROLE_WORKER = 'worker'
ROLE_ADMIN = 'admin'
ROLE_SYSTEM = 'system'

# Admins may do anything a worker may do. Encoded here rather than checked
# ad hoc, so a transition added later inherits the rule automatically.
_ROLE_IMPLIES: Dict[str, Set[str]] = {
    ROLE_ADMIN: {ROLE_ADMIN, ROLE_WORKER, ROLE_SYSTEM},
    ROLE_WORKER: {ROLE_WORKER},
    ROLE_SYSTEM: {ROLE_SYSTEM},
}


# --------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------- #

# (from, to) -> roles permitted to make the move.
#
# Read this as the definition of the workflow. Anything absent is illegal:
# there is no NEW -> SUBMITTED, no REJECTED -> APPROVED, and no path out of
# APPROVED or REJECTED at all.
TRANSITIONS: Dict[Tuple[str, str], FrozenSet[str]] = {
    # Intake and assignment -- administrative.
    (NEW, ASSIGNED):              frozenset({ROLE_ADMIN}),
    # SYSTEM as well as ADMIN: the Eagle sync retires a task that has left
    # the external platform's available list, which means another worker on
    # that platform took it. That is a real, automated, non-administrative
    # act, and the alternative -- leaving phantom tasks in the queue for our
    # workers to click and fail -- is worse.
    (NEW, CANCELLED):             frozenset({ROLE_ADMIN, ROLE_SYSTEM}),
    (ASSIGNED, NEW):              frozenset({ROLE_ADMIN}),      # unassign
    (ASSIGNED, CANCELLED):        frozenset({ROLE_ADMIN}),

    # Claiming. A worker may claim an unassigned task or one earmarked for
    # them; the ownership check lives in the service, because it depends on
    # WHICH worker rather than on the state.
    (NEW, CLAIMED):               frozenset({ROLE_WORKER}),
    (ASSIGNED, CLAIMED):          frozenset({ROLE_WORKER}),

    # Release -- back to the pool, or back to the worker it was assigned to.
    (CLAIMED, NEW):               frozenset({ROLE_WORKER, ROLE_ADMIN}),
    (CLAIMED, ASSIGNED):          frozenset({ROLE_ADMIN}),
    (IN_PROGRESS, NEW):           frozenset({ROLE_WORKER, ROLE_ADMIN}),
    (REVIEW, NEW):                frozenset({ROLE_ADMIN}),
    (READY_TO_SUBMIT, NEW):       frozenset({ROLE_ADMIN}),

    # The working path.
    (CLAIMED, IN_PROGRESS):       frozenset({ROLE_WORKER, ROLE_SYSTEM}),
    (IN_PROGRESS, REVIEW):        frozenset({ROLE_WORKER, ROLE_SYSTEM}),
    (REVIEW, IN_PROGRESS):        frozenset({ROLE_WORKER}),     # back to editing
    (REVIEW, READY_TO_SUBMIT):    frozenset({ROLE_WORKER}),
    (READY_TO_SUBMIT, REVIEW):    frozenset({ROLE_WORKER}),

    # Submission. SYSTEM performs the act because Phase 4's integration
    # service does it; a worker triggers it but does not carry it out.
    (READY_TO_SUBMIT, SUBMITTED): frozenset({ROLE_WORKER, ROLE_SYSTEM}),

    # Outcome -- reported by the external platform, recorded by an admin or
    # by the integration service. Never by the worker who did the work.
    (SUBMITTED, APPROVED):        frozenset({ROLE_ADMIN, ROLE_SYSTEM}),
    (SUBMITTED, REJECTED):        frozenset({ROLE_ADMIN, ROLE_SYSTEM}),

    # REJECTED HAS NO WAY OUT. The external platform's decision is final --
    # there is no appeal and no resubmission, so a rejected task cannot move
    # to IN_PROGRESS, CANCELLED, or anywhere else. The only way to do this
    # work again is a brand new task, which is exactly what TERMINAL_STATES
    # is for.

    # Failure. Any active state can fail, and only a retry brings it back.
    (CLAIMED, FAILED):            frozenset({ROLE_SYSTEM, ROLE_ADMIN}),
    (IN_PROGRESS, FAILED):        frozenset({ROLE_SYSTEM, ROLE_ADMIN}),
    (REVIEW, FAILED):             frozenset({ROLE_SYSTEM, ROLE_ADMIN}),
    (READY_TO_SUBMIT, FAILED):    frozenset({ROLE_SYSTEM, ROLE_ADMIN}),
    (SUBMITTED, FAILED):          frozenset({ROLE_SYSTEM, ROLE_ADMIN}),
    (FAILED, NEW):                frozenset({ROLE_ADMIN}),      # retry
    (FAILED, IN_PROGRESS):        frozenset({ROLE_WORKER, ROLE_ADMIN}),
    (FAILED, CANCELLED):          frozenset({ROLE_ADMIN}),
}


class IllegalTransition(Exception):
    """
    Raised for a move the machine does not define, or one the actor's role
    does not permit.

    `.safe_message` follows the convention already established in
    core.services.ai_assist.errors: the full reason is for logs, and a
    client sees a sentence that names no internals.
    """

    def __init__(self, source: str, target: str, role: Optional[str] = None):
        self.source = source
        self.target = target
        self.role = role
        if (source, target) not in TRANSITIONS:
            detail = f'{source} -> {target} is not a defined transition.'
        else:
            detail = (
                f'{source} -> {target} requires one of '
                f'{sorted(TRANSITIONS[(source, target)])}; actor role is {role!r}.'
            )
        self.detail = detail
        super().__init__(detail)

    @property
    def safe_message(self) -> str:
        return (
            f'A task cannot move from "{self.source}" to "{self.target}"'
            + (' with your permissions.' if self.role else '.')
        )


def allowed_transitions(source: str, role: str = ROLE_ADMIN) -> Set[str]:
    """Every state `role` may move a task in `source` to. Drives the UI so
    it offers exactly the buttons the server will accept."""
    implied = _ROLE_IMPLIES.get(role, set())
    return {
        target for (start, target), roles in TRANSITIONS.items()
        if start == source and implied & roles
    }


def can_transition(source: str, target: str, role: str) -> bool:
    roles = TRANSITIONS.get((source, target))
    if roles is None:
        return False
    return bool(_ROLE_IMPLIES.get(role, set()) & roles)


def check_transition(source: str, target: str, role: str) -> None:
    """Raises IllegalTransition unless the move is legal for this role."""
    if not can_transition(source, target, role):
        raise IllegalTransition(source, target, role)


# Timestamp field set when a task ENTERS each state. Kept beside the machine
# so adding a state cannot leave its timestamp unrecorded -- the performance
# figures in Phase 3 are computed from exactly these.
ENTRY_TIMESTAMP_FIELD: Dict[str, str] = {
    ASSIGNED: 'assigned_at',
    CLAIMED: 'claimed_at',
    IN_PROGRESS: 'started_at',
    REVIEW: 'review_started_at',
    READY_TO_SUBMIT: 'ready_at',
    SUBMITTED: 'submitted_at',
    APPROVED: 'approved_at',
    REJECTED: 'rejected_at',
    FAILED: 'failed_at',
    CANCELLED: 'cancelled_at',
}
