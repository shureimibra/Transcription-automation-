"""
Authorization for the transcription API.

WHAT THIS CLOSES. Phase 1 recorded that this entire API ran under the project
default `AllowAny` with no authentication classes and no ownership concept.
That was not a theoretical gap: an anonymous request to
`GET /api/v1/transcriptions/` returned 224 jobs with full segment text,
speaker labels and Eagle source URLs. On one laptop that was survivable. With
six people reaching it over the internet it is a data breach, so it is closed
before anything is deployed.

ONE PLACE DECIDES VISIBILITY. `visible_jobs_for` is the only function that
answers "which jobs may this caller see". Every list endpoint filters through
it and every detail endpoint checks through it. A second copy of this rule
would be a second chance to get it subtly wrong -- the same reasoning as
`workforce.permissions.visible_tasks_for`, and deliberately the same shape so
that the two read alike.

A NULL OWNER IS ADMIN-ONLY. The 224 pre-existing rows have no owner to
attribute them to. Treating null as "belongs to everyone" would reinstate the
exact leak being closed, so null is treated as "belongs to the operator".

...AND THAT IS WHY WORKFORCE TASKS NEED A SECOND ARM. The warm pool creates
jobs on nobody's behalf -- there is no user in the room when a task is
pre-processed -- so every warmed transcript is ownerless and, by the rule
above, invisible to the very tasker meant to review it. The symptom was a
bare "Not found." on the review screen for a task the queue had just
advertised as ready.

The fix is NOT to relax the null rule. It is that a transcript attached to a
task you can already see is a transcript you may read: you are either
holding that task or deciding whether to take it, and the queue has already
shown you it exists. `workforce.permissions.visible_tasks_for` is the single
place that decides which tasks those are, so this defers to it rather than
re-deriving the answer.
"""
from django.db.models import Q
from rest_framework.permissions import BasePermission

from .models import AIRevision, TranscriptionJob


def is_admin(user) -> bool:
    """
    Administrator, by either signal.

    Mirrors `workforce.permissions.is_admin`: the explicit workforce role is
    primary, Django's staff flag is the compatibility path so a superuser is
    never locked out of their own application.
    """
    if not (user and user.is_authenticated):
        return False
    if user.is_staff or user.is_superuser:
        return True
    try:
        from apps.workforce import roles as wf_roles
        from apps.workforce.permissions import account_role
        return account_role(user) == wf_roles.ADMIN
    except Exception:
        # The workforce app must never be able to break the engine's
        # authorization by failing to import.
        return False


class IsAuthenticatedUser(BasePermission):
    """
    Signed in. Nothing more.

    Used for reference data -- supported languages, AI capabilities -- which
    is not sensitive but has no reason to be world-readable either.
    """

    message = 'Authentication is required.'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


def _job_ids_from_visible_tasks(user):
    """
    Jobs reachable because a workforce task points at them.

    Defers to `workforce.permissions.visible_tasks_for`, which is the single
    place that decides which tasks a person may see -- a worker's own claims
    plus the open pool. Re-deriving that rule here would be a second chance
    to get it subtly wrong, and the two would drift.

    Returns an empty tuple if the workforce app is unavailable, so a failure
    over there can only ever REMOVE access, never grant it.
    """
    try:
        from apps.workforce.permissions import visible_tasks_for
        return (visible_tasks_for(user)
                .exclude(job__isnull=True)
                .values_list('job_id', flat=True))
    except Exception:
        return ()


def visible_jobs_for(user):
    """
    The job queryset a caller may see. THE one place that decides this.

    Administrators see everything, including the ownerless rows that predate
    the field. Everyone else sees what they created, plus the transcripts
    attached to tasks they can already see -- which is how a tasker reaches
    a warmed transcript that was created on nobody's behalf.
    """
    if not (user and user.is_authenticated):
        return TranscriptionJob.objects.none()
    if is_admin(user):
        return TranscriptionJob.objects.all()
    return TranscriptionJob.objects.filter(
        Q(owner=user) | Q(pk__in=_job_ids_from_visible_tasks(user))
    ).distinct()


def may_access_job(user, job) -> bool:
    """Whether `user` may read or act on `job`."""
    if not (user and user.is_authenticated) or job is None:
        return False
    if is_admin(user):
        return True
    if job.owner_id == user.id:
        return True
    # Attached to a task this person may see. Same rule as the queryset
    # above, asked about one row.
    return job.pk in set(_job_ids_from_visible_tasks(user))


def may_access_revision(user, revision) -> bool:
    """
    Whether `user` may reach an AIRevision.

    Checked through the REVISION'S JOB, not through `revision.owner`. A
    revision inherits the confidentiality of the transcript it derives from,
    and `owner` on a revision records who requested it -- which is not the
    same question and would let a second person's revision of someone else's
    transcript escape the boundary.
    """
    if not (user and user.is_authenticated) or revision is None:
        return False
    if is_admin(user):
        return True
    return may_access_job(user, revision.job)


class JobAccessPermission(BasePermission):
    """Object-level check for anything addressed by a job."""

    message = 'You do not have access to that transcription.'

    def has_object_permission(self, request, view, obj):
        if isinstance(obj, TranscriptionJob):
            return may_access_job(request.user, obj)
        if isinstance(obj, AIRevision):
            return may_access_revision(request.user, obj)
        job = getattr(obj, 'job', None)
        if job is not None:
            return may_access_job(request.user, job)
        return is_admin(request.user)
