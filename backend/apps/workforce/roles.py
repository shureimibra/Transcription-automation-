"""
What kind of account someone has, as data.

WHY THIS EXISTS. Before this module, a person's role was INFERRED: admin
meant `user.is_staff`, and worker meant "has an active WorkerProfile".
Nothing else was expressible, so a user who was allowed to use the
transcription studio but was not part of the task workforce could not exist
-- the login view refused them outright with "This account is not active for
task work."

Inference also gets the edge case wrong in a way that matters. If "not a
worker" is treated as "tool user", then SUSPENDING a tasker silently
converts them into a tool user and hands them the studio, which is the
opposite of what suspending someone is for. A stored role cannot drift like
that: suspending changes the worker's STATUS, never their ROLE.

A FEW ROLES, NOT A PERMISSION MATRIX. Six to thirty people doing two jobs do
not need per-object ACLs, and a matrix nobody can hold in their head is how
authorization bugs get written. What this must support is being EXTENDED --
a reviewer or a team lead later -- which is why the roles are a choices
tuple plus a capability function, and not a chain of `if` statements spread
across views.

ROLE IS NOT PERMISSION. This module answers "what kind of account is this".
It never answers "may this person touch this row" -- that stays in
permissions.py, which reads roles from here. Keeping the two apart is what
stops a role check from quietly becoming an ownership check.
"""

#: Full operational control: every task, every worker, the analytics.
ADMIN = 'admin'

#: A tasker. Claims tasks, works them, submits them; measured on them.
TASKER = 'tasker'

#: Allowed to use the transcription studio, but NOT part of the workforce.
#: Sees no queue, no assignments, no other worker's anything.
TOOL = 'tool'

#: An outside Eagle worker. Claims on Eagle themselves, then looks the task
#: up here by its exact ID and edits a PRIVATE copy of the prepared draft.
#: Not in the workforce: no queue, no claiming, no submitting, no studio.
EAGLE_TASKER = 'eagle_tasker'

ROLE_CHOICES = [
    (ADMIN, 'Administrator'),
    (TASKER, 'Tasker'),
    (TOOL, 'Tool user'),
    (EAGLE_TASKER, 'Eagle tasker'),
]

ALL_ROLES = frozenset(code for code, _ in ROLE_CHOICES)

#: The default for an account created with nothing said about it. TOOL is the
#: least privileged of the three: it grants the studio and nothing else. A
#: new account must never default into the workforce.
DEFAULT_ROLE = TOOL


# --------------------------------------------------------------------- #
# Capabilities
# --------------------------------------------------------------------- #
#
# What each role may reach, as a table rather than as conditionals scattered
# through views and components. Adding a role means adding a row; it does not
# mean auditing every `if` in the codebase for a branch that forgot it.
#
# These are COARSE surface gates -- "may this kind of account reach this area
# at all". Everything finer (which task, whose task) is object-level and
# lives in permissions.py.

#: May reach the workforce task surface: queue, claiming, task workspace.
WORKFORCE = 'workforce'

#: May reach administrative surfaces: all tasks, worker management, analytics.
ADMINISTRATION = 'administration'

#: May reach the standalone transcription studio.
STUDIO = 'studio'

#: Is measured: appears in the performance report, has throughput tracked.
MEASURED = 'measured'

#: May look up a task by its exact Eagle ID and work a private copy of it.
#: Outside taskers ONLY -- not administrators. The two areas share no
#: account; to see what a tasker sees, sign in as a tasker.
EAGLE = 'desk'

_CAPABILITIES = {
    ADMIN:        frozenset({WORKFORCE, ADMINISTRATION, STUDIO}),
    TASKER:       frozenset({WORKFORCE, STUDIO, MEASURED}),
    TOOL:         frozenset({STUDIO}),
    # NO STUDIO. The studio would give an outside account our whole
    # transcription engine for any URL; this role exists to reuse work
    # already done, not to start new work on our provider budget.
    EAGLE_TASKER: frozenset({EAGLE}),
}


def capabilities(role) -> frozenset:
    """Everything `role` may reach. An unknown role gets nothing."""
    return _CAPABILITIES.get(role, frozenset())


def can(role, capability) -> bool:
    """
    Whether `role` may reach `capability`.

    Fails closed: an unrecognised role -- a value written by an older
    version, or a typo in a fixture -- is refused rather than defaulted into
    something permissive.
    """
    return capability in capabilities(role)


def label(role) -> str:
    """Human-readable name, falling back to the raw value rather than to
    something plausible-but-wrong."""
    return dict(ROLE_CHOICES).get(role, str(role))
