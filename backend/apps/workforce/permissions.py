"""
Server-side authorization. The client is never consulted about identity.

THE RULE THIS FILE EXISTS TO ENFORCE: the worker is derived from
`request.user`, never from anything in the request body or query string. A
payload containing `worker_id` is ignored -- if it were honoured, every
ownership check below would be bypassable by editing one field.

Two layers, both required:

  * class-level  -- may this ROLE reach this endpoint at all
  * object-level -- may this PERSON touch this ROW

DRF calls `has_object_permission` only for objects fetched through
`get_object()`. Any view that filters a queryset by hand must scope that
queryset itself; `visible_tasks_for` below is the one place that decides
what a worker can see, so no view has to re-derive it.
"""
from rest_framework.permissions import BasePermission, SAFE_METHODS

from . import roles
from .models import Account, Task, WorkerProfile


def account_of(user):
    """
    The caller's Account, created on first sight if missing.

    LAZY CREATION IS DELIBERATE. Users can arrive from outside this app --
    `createsuperuser`, the Django admin, a fixture, a future SSO -- and an
    account without a role row must not 500 the whole API. Creating it at
    the default (TOOL, the least privileged role) is safe: it grants the
    studio and nothing else, and an administrator can raise it.

    Cached per request, like `worker_profile` below, so a permission check
    that runs several times in one view costs one query.
    """
    if not user or not user.is_authenticated:
        return None
    cached = getattr(user, '_wf_account_cache', None)
    if cached is not None:
        return cached
    # A staff user with no row yet is an administrator, not a tool user --
    # otherwise `createsuperuser` would produce someone who cannot reach
    # the admin surface of their own application.
    default = roles.ADMIN if (user.is_staff or user.is_superuser) else roles.DEFAULT_ROLE
    account, _ = Account.objects.get_or_create(user=user, defaults={'role': default})
    user._wf_account_cache = account
    return account


def account_role(user) -> str:
    """
    The caller's role, or None when not authenticated.

    Named `account_role` rather than `role` on purpose: `services.role_of`
    already returns the STATE-MACHINE actor role (worker/admin/system),
    which is a different question with overlapping vocabulary. Confusing
    the two would put authorization decisions in the state machine.
    """
    account = account_of(user)
    return account.role if account else None


def has_capability(user, capability) -> bool:
    return roles.can(account_role(user), capability)


def training_mode_active() -> bool:
    """
    WORKFORCE_TRAINING_MODE, read once per call so a settings override in a
    test (`override_settings`) takes effect without a process restart.

    Scoped narrowly on purpose: see the setting's own docstring in
    settings.py for exactly which two endpoints this affects and why
    Submit-to-Eagle is deliberately not one of them.
    """
    from django.conf import settings
    return bool(getattr(settings, 'WORKFORCE_TRAINING_MODE', False))


def is_admin(user) -> bool:
    """
    ADMIN accepts either signal. The explicit role is the primary one; the
    Django staff flag is kept because superusers created outside this app
    must not be locked out of it. The migration backfills staff users to
    the ADMIN role so the two agree, and they can only disagree if someone
    sets one without the other afterwards.
    """
    if not (user and user.is_authenticated):
        return False
    if user.is_staff or user.is_superuser:
        return True
    return account_role(user) == roles.ADMIN


def is_tasker(user) -> bool:
    """In the workforce. Says nothing about whether they are currently
    ALLOWED to work -- that is `WorkerProfile.is_active_worker`."""
    return account_role(user) == roles.TASKER


def is_tool_user(user) -> bool:
    return account_role(user) == roles.TOOL


def worker_profile(user):
    """The caller's profile, or None. Cached per request to avoid a query
    for every permission check on the same view."""
    if not user or not user.is_authenticated:
        return None
    cached = getattr(user, '_wf_profile_cache', None)
    if cached is not None:
        return cached if cached is not False else None
    profile = WorkerProfile.objects.filter(user=user).select_related('user').first()
    user._wf_profile_cache = profile if profile else False
    return profile


class IsAuthenticatedWorkerOrAdmin(BasePermission):
    """
    Authenticated, and either an admin or an ACTIVE worker.

    A suspended worker keeps their login but loses task access -- that
    separation is deliberate, because disabling the Django account and
    suspending the profile are different administrative acts with different
    consequences (see WorkerProfile.is_active_worker).
    """

    message = 'An active worker account is required.'

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if is_admin(request.user):
            return True
        # The role gate comes first. A tool user is refused here by what
        # their account IS, not incidentally by the absence of a
        # WorkerProfile -- so the refusal survives someone later creating a
        # profile row for them by mistake.
        if not has_capability(request.user, roles.WORKFORCE):
            return False
        profile = worker_profile(request.user)
        return bool(profile and profile.is_active_worker)


class IsAdmin(BasePermission):
    """Administrative endpoints. Worker accounts are refused outright."""

    message = 'Administrator access is required.'

    def has_permission(self, request, view):
        return is_admin(request.user)


class IsAdminOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return is_admin(request.user)


class TaskAccessPermission(BasePermission):
    """
    Object-level task access.

    Admins: everything. Workers: a task they hold, a task assigned to them,
    or an unclaimed task in the open pool -- the last because a worker has
    to be able to see something in order to claim it.

    WRITES ARE NARROWER THAN READS. Seeing an open task does not confer the
    right to modify it; only the holder may. Claiming is the one write that
    is legal on a task you do not yet hold, and it has its own service-level
    check that re-reads the row under a lock rather than trusting this.
    """

    message = 'You do not have access to that task.'

    def has_object_permission(self, request, view, obj):
        if is_admin(request.user):
            return True
        if not isinstance(obj, Task):
            return False
        profile = worker_profile(request.user)
        if profile is None or not profile.is_active_worker:
            return False

        if request.method in SAFE_METHODS:
            return obj.is_visible_to(profile)

        # Claiming is the one write legal on a task you do not yet hold.
        #
        # The bar here is deliberately "not reserved for someone else"
        # rather than "currently visible". If it were visibility, a worker
        # who lost a race by half a second would get 404 -- the task
        # vanished from their queryset the moment the winner claimed it --
        # which reads as a bug rather than as "someone beat you to it". The
        # service re-reads the row under a lock and returns a truthful 409
        # instead. That leaks nothing: the loser had the task on screen a
        # moment earlier, which is how they came to click claim.
        if getattr(view, 'action', None) == 'claim':
            return obj.assigned_to_id in (None, profile.id)
        return obj.claimed_by_id == profile.id


def visible_tasks_for(user):
    """
    The task queryset a caller may see.

    THE ONE PLACE that decides worker visibility. Every list endpoint filters
    through this rather than writing its own `Q(...)`, because a second copy
    of this rule is a second chance to get it subtly wrong and leak another
    worker's queue.
    """
    from django.db.models import Q

    if is_admin(user):
        return Task.objects.all()

    # Not in the workforce -- a tool user sees no tasks at all, which is
    # different from a tasker who happens to have none.
    if not has_capability(user, roles.WORKFORCE):
        return Task.objects.none()

    profile = worker_profile(user)
    if profile is None or not profile.is_active_worker:
        return Task.objects.none()

    from . import states
    return Task.objects.filter(
        # mine, held or earmarked
        Q(claimed_by=profile)
        | Q(assigned_to=profile)
        # or free for anyone to take
        | Q(status__in=states.CLAIMABLE_STATES, claimed_by__isnull=True,
            assigned_to__isnull=True)
    ).distinct()
