"""
The operational layer: who works, what they work on, and what happened.

WRAPPED AROUND THE ENGINE, NOT MERGED INTO IT. A Task points at the existing
TranscriptionJob and AIRevision rather than duplicating transcript data, so
the transcription pipeline neither knows nor cares that a workforce exists.
That separation is what lets Phase 2 ship without touching ASR, diarization,
translation or exports.

WHY A PROFILE RATHER THAN A CUSTOM USER MODEL. Swapping AUTH_USER_MODEL after
17 migrations have been applied is the one schema change Django genuinely
punishes, and the payoff would be marginal: the fields a worker needs are
operational (worker code, activity, status), not authentication fields.
Django's User keeps the credentials, and everything operational hangs off
WorkerProfile. AIRevision.owner and AIUsageRecord.owner already point at
AUTH_USER_MODEL, so they keep working untouched.
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from . import roles, states


class Account(models.Model):
    """
    What kind of account a user has. One row per user, always.

    WHY THIS IS SEPARATE FROM WorkerProfile. WorkerProfile carries
    TASKER-SPECIFIC operational data -- worker code, concurrency limit,
    activity, suspension. A tool user has none of that and should not be
    given an empty one just to have a role, and an administrator who never
    claims a task should not need a worker code either. Role is a property
    of the ACCOUNT; being a worker is a job the account may also hold.

    WHY ROLE IS STORED AND NOT DERIVED. Deriving it from
    "has an active WorkerProfile" gets suspension exactly backwards:
    suspending a tasker would flip them to the tool role and hand them the
    studio, when the whole point of suspending someone is to take access
    away. Status and role are different questions and are stored
    separately.

    ADMIN IS STILL ALSO `is_staff`. Django's own admin site and
    `createsuperuser` key off `is_staff`/`is_superuser`, and a superuser
    locked out of their own application by a role row would be a trap. So
    `is_admin()` in permissions.py accepts EITHER signal, and the
    `admin_role` migration backfills existing staff users to ADMIN so the
    two agree from the start. New administrators should be given the role;
    the flag remains the compatibility path, not the primary one.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='workforce_account',
    )
    role = models.CharField(
        max_length=16, choices=roles.ROLE_CHOICES, default=roles.DEFAULT_ROLE,
        # Indexed because "list every tasker" and "list every admin" are
        # both admin-screen queries, and the table grows with headcount.
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['user__username']

    def __str__(self):
        return f'{self.user.username} ({roles.label(self.role)})'

    def can(self, capability) -> bool:
        return roles.can(self.role, capability)


class WorkerProfile(models.Model):
    """
    The operational identity behind a Django user.

    Deliberately holds no personal information beyond a display name: the
    platform needs to know who did what, not who they are. Anything more
    would be data to protect for no operational gain.
    """

    ACTIVE = 'active'
    INACTIVE = 'inactive'
    SUSPENDED = 'suspended'
    STATUS_CHOICES = [
        (ACTIVE, 'Active'),
        (INACTIVE, 'Inactive'),
        (SUSPENDED, 'Suspended'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='worker_profile',
    )
    # Short human-readable handle used in the UI and in exports, so an
    # operator never has to quote a UUID out loud.
    worker_code = models.CharField(max_length=32, unique=True)
    display_name = models.CharField(max_length=120, blank=True, default='')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=ACTIVE)

    # Set on every authenticated action. Cheap operational signal -- "is
    # anyone actually working right now" -- without a heartbeat mechanism.
    last_activity_at = models.DateTimeField(null=True, blank=True)

    # A ceiling on simultaneous claims. Six workers sharing one CPU-bound
    # ASR box is the real constraint (see the Phase 1 performance figures),
    # so letting one worker claim twenty tasks helps nobody.
    max_concurrent_tasks = models.PositiveSmallIntegerField(default=3)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['worker_code']
        indexes = [
            models.Index(fields=['status'], name='wf_worker_status_idx'),
        ]

    def __str__(self):
        return f'{self.worker_code} ({self.display_name or self.user.username})'

    @property
    def is_active_worker(self) -> bool:
        """Both gates must be open: a disabled Django account and a
        suspended profile are different administrative acts, and either
        one alone is enough to stop work."""
        return self.status == self.ACTIVE and self.user.is_active

    @property
    def role(self) -> str:
        return states.ROLE_ADMIN if self.user.is_staff else states.ROLE_WORKER

    def touch(self):
        self.last_activity_at = timezone.now()
        self.save(update_fields=['last_activity_at'])


class Task(models.Model):
    """
    One unit of work, from intake to outcome.

    HOLDS NO TRANSCRIPT. `job` and `deliverable_revision` are foreign keys
    into the existing engine; the words live where they already lived. A
    task carries only what the workforce layer needs: who owns it, what
    state it is in, and when each state was entered.
    """

    TRANSCRIPTION = 'transcription'
    TRANSLATION = 'translation'
    KIND_CHOICES = [
        (TRANSCRIPTION, 'Transcription'),
        (TRANSLATION, 'Translation'),
    ]

    PRIORITY_LOW = 10
    PRIORITY_NORMAL = 50
    PRIORITY_HIGH = 90

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # ---- external identity (Phase 4 fills these in) ------------------- #
    # Nullable because a task can be created manually before any external
    # system knows about it. Unique WITH the source rather than alone: the
    # transcription and translation products number their tasks
    # independently, so 4130 exists in both and means different things.
    external_task_id = models.CharField(max_length=64, blank=True, default='')
    external_source = models.CharField(max_length=32, blank=True, default='')
    source_url = models.URLField(max_length=1000, blank=True, default='')

    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=TRANSCRIPTION)
    title = models.CharField(max_length=255, blank=True, default='')
    #: Eagle's project label -- "Law class", "Speech", "Business Translation".
    #:
    #: IT DECIDES THE TRANSCRIPTION STANDARD, which is the difference between
    #: a document that is right and one that is wrong in every line. See
    #: PROJECT_STANDARDS in the quality package for the mapping, which came
    #: from the coach directly.
    #:
    #: Blank when the platform did not give us one; callers then fall back to
    #: EAGLE_TRANSCRIPTION_STANDARD, and should say on screen that they
    #: assumed rather than knew.
    project = models.CharField(max_length=120, blank=True, default='')
    source_language = models.CharField(max_length=10, blank=True, default='')
    target_language = models.CharField(max_length=10, blank=True, default='')
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    #: What the PLATFORM says this task costs, in whole minutes.
    #:
    #: Kept because our own estimate disagrees with it and the platform is
    #: the one doing the billing: a 5m04s file rounds up to 6 by any honest
    #: reading, and Eagle charges 5. Estimating high blocks a claim that
    #: would have succeeded, which is a task lost to arithmetic.
    #:
    #: Null when the platform did not say; callers then fall back to the
    #: rounded-up estimate, which is the safe direction to guess.
    minutes_required = models.PositiveSmallIntegerField(null=True, blank=True)
    #: THE PLATFORM'S CLOCK, as the platform set it.
    #:
    #: Eagle gives one hour from claim to submission, and a task that expires
    #: costs the purchased minutes AND the work already done -- so this is the
    #: highest-stakes number a worker sees. It was arriving on every synced
    #: row and being thrown away, which meant DeadlineClock had nothing to
    #: count down from and silently rendered nothing at all. A worker on a
    #: one-hour clock could not see the clock.
    #:
    #: Null until the platform sets one, which for transcription is at claim.
    deadline_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=24, choices=states.STATUS_CHOICES, default=states.NEW,
    )
    priority = models.PositiveSmallIntegerField(default=PRIORITY_NORMAL)

    # ---- ownership ---------------------------------------------------- #
    # assigned_to is an administrative earmark; claimed_by is possession.
    # A task can be assigned without being claimed (waiting for that worker
    # to start) but never claimed without being held.
    assigned_to = models.ForeignKey(
        WorkerProfile, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_tasks',
    )
    claimed_by = models.ForeignKey(
        WorkerProfile, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='claimed_tasks',
    )
    # Non-null means "claimed_by is a RESERVATION, not yet a real claim" --
    # set by an atomic conditional UPDATE before the external Eagle call
    # eagle_claim.claim_for_worker makes, cleared the moment that resolves
    # either way (finalized into a real claim, or rolled back on refusal/
    # error). Its purpose is closing the exact race BEFORE_DEPLOYMENT.md's
    # own item 1 named: two workers racing one task used to both pass the
    # "is this free?" check and both send a real claim POST to Eagle,
    # relying on Eagle's own 422 as the only thing stopping a double claim.
    # Also what the stranded-reservation sweeper keys off: a row where this
    # is set and old means a worker's process died mid-flight, not that the
    # task is genuinely held.
    claim_reserved_at = models.DateTimeField(null=True, blank=True)

    # ---- links into the existing engine ------------------------------- #
    # SET_NULL, not CASCADE: deleting a job must not erase the operational
    # record that the work happened and who did it.
    job = models.ForeignKey(
        'transcriptions.TranscriptionJob', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='workforce_tasks',
    )
    # What the recording is ABOUT, for the submission header.
    #
    # Eagle's own `title` is the media filename -- "C 64.mp3" -- and the
    # accepted transcriptions carry a real subject line instead:
    # "Transcription Job: How to Build Strong Habits". A filename in that
    # position tells the reader nothing and marks the document as machine
    # output.
    #
    # Generated once from the transcript during warming, then stored: it is
    # part of the deliverable, so it must not change between the preview a
    # worker reads and the document that is sent.
    topic = models.CharField(max_length=200, blank=True, default='')

    # The tasker's polished French, pasted back from their own assistant.
    #
    # PROSE, NOT SEGMENTS, because Eagle's translation deliverable is prose:
    # no timestamps, no speaker labels. There is nothing to realign, so
    # storing paragraphs is honest where storing pseudo-segments would
    # invent a structure the document does not have.
    #
    # When present it WINS over `deliverable_revision` at render time: it is
    # the later, human-approved version of the same document. The revision
    # is kept either way, so the machine draft stays available for
    # comparison and the improvement is measurable.
    polished_text = models.TextField(blank=True, default='')
    polished_by = models.ForeignKey(
        'WorkerProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='polished_tasks',
    )
    polished_at = models.DateTimeField(null=True, blank=True)

    # A TRANSCRIPTION task's counterpart to polished_text -- and
    # deliberately NOT the same shape.
    #
    # NEVER OVERWRITES THE TRANSCRIPT. TranscriptCheckPromptView hands back
    # leads to go verify against the audio, not a document to paste over
    # the transcript -- a worker's assistant has never heard the recording,
    # so a rewrite from it would repeat whatever it misheard with more
    # confidence, not less. What is stored here is an AUDIT TRAIL: proof the
    # step happened, and a record of what it found, for a coach reviewing a
    # rejection later. The actual correction still goes through the normal
    # per-line editing on this screen.
    #
    # `accuracy_check_by` set is what run_quality_gate checks for: the whole
    # point of storing this is that a submission is refused without it.
    accuracy_check_notes = models.TextField(blank=True, default='')
    accuracy_check_by = models.ForeignKey(
        'WorkerProfile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='accuracy_checked_tasks',
    )
    accuracy_check_at = models.DateTimeField(null=True, blank=True)

    deliverable_revision = models.ForeignKey(
        'transcriptions.AIRevision', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='workforce_tasks',
        help_text='The AI revision chosen as the submission deliverable.',
    )

    # ---- lifecycle timestamps ----------------------------------------- #
    # One per state entry. Written by the service layer from
    # states.ENTRY_TIMESTAMP_FIELD, so a new state cannot be added without
    # its timestamp -- every performance figure in Phase 3 derives from
    # these rather than from scanning the event log.
    created_at = models.DateTimeField(auto_now_add=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    review_started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ---- diagnostics --------------------------------------------------- #
    # Client-safe text only. The convention from
    # core.services.ai_assist.errors applies: full detail goes to the log,
    # never into a field that is serialized back to a browser.
    failure_reason = models.TextField(blank=True, default='')
    rejection_reason = models.TextField(blank=True, default='')
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-priority', 'created_at']
        indexes = [
            # The worker queue: "what can I claim", ordered.
            models.Index(fields=['status', 'priority', 'created_at'],
                         name='wf_task_queue_idx'),
            # "my tasks" for each of the two ownership axes.
            models.Index(fields=['claimed_by', 'status'], name='wf_task_claimed_idx'),
            models.Index(fields=['assigned_to', 'status'], name='wf_task_assigned_idx'),
            # Phase 4 reconciliation against the external platform.
            models.Index(fields=['external_source', 'external_task_id'],
                         name='wf_task_external_idx'),
        ]
        constraints = [
            # The same external task must not enter the system twice.
            # Scoped to rows that HAVE an external id, so any number of
            # manually-created tasks can coexist. Postgres honours the
            # partial index; this is one of the constraints SQLite would
            # not have enforced the same way.
            models.UniqueConstraint(
                fields=['external_source', 'external_task_id'],
                condition=models.Q(external_task_id__gt=''),
                name='wf_task_unique_external_ref',
            ),
        ]

    def __str__(self):
        label = self.external_task_id or str(self.id)[:8]
        return f'Task {label} [{self.status}]'

    # ------------------------------------------------------------------ #
    # Derived state -- read-only helpers, no writes
    # ------------------------------------------------------------------ #

    @property
    def is_terminal(self) -> bool:
        return self.status in states.TERMINAL_STATES

    @property
    def is_held(self) -> bool:
        return self.status in states.WORKER_HELD_STATES

    @property
    def is_claimable(self) -> bool:
        return self.status in states.CLAIMABLE_STATES and self.claimed_by_id is None

    def is_visible_to(self, profile: 'WorkerProfile') -> bool:
        """
        Whether a worker may see this task at all.

        Unclaimed work in the open pool is visible to everyone -- that is
        what makes claiming possible. Once held, a task is visible only to
        its holder. Admins bypass this entirely at the permission layer.
        """
        if profile is None:
            return False
        if self.claimed_by_id == profile.id or self.assigned_to_id == profile.id:
            return True
        return self.is_claimable and self.assigned_to_id is None

    def processing_seconds(self):
        """Claim to ready. None until both ends exist."""
        if self.claimed_at and self.ready_at:
            return (self.ready_at - self.claimed_at).total_seconds()
        return None

    def review_seconds(self):
        if self.review_started_at and self.ready_at:
            return (self.ready_at - self.review_started_at).total_seconds()
        return None

    def turnaround_seconds(self):
        """Creation to final outcome."""
        end = self.approved_at or self.rejected_at or self.submitted_at
        return (end - self.created_at).total_seconds() if end else None


def ready_for_work_q():
    """
    THE ONE DEFINITION OF "READY RIGHT NOW", as a database filter.

    A completed transcript job is not enough for a TRANSLATION task, which
    also needs deliverable_revision set -- a translate pass can fail while
    the transcript underneath it still shows job__status='completed', and a
    filter that stops at the job status alone counts that task as ready when
    a tasker who claims it would sit waiting on the machine.

    THIS FUNCTION EXISTS BECAUSE THAT DISTINCTION DRIFTED OUT OF SYNC THREE
    SEPARATE TIMES: warmpool.run_cycle's warm_now count, TaskViewSet's
    ordering annotation, and WorkQueueStatusView.ready_now all re-implemented
    it independently and one of them was wrong at any given time. Every
    queryset-level "is this ready" check should filter on this, not
    re-derive the Q expression locally.

    Kept apart from warmpool.is_warm() and TaskSerializer.get_is_ready,
    which ask the identical question of a Task object already fetched into
    memory rather than as a filter -- same rule, different shape, because a
    Q object is not a useful way to test one in-memory instance.
    """
    from django.db.models import Q
    return Q(job__status='completed') & (
        ~Q(kind=Task.TRANSLATION) | Q(deliverable_revision__isnull=False)
    )


class TaskEvent(models.Model):
    """
    Append-only record of everything that happened to a task.

    IMMUTABLE BY CONTRACT: `save()` refuses to update an existing row and
    there is no update path anywhere in the app. An audit log that can be
    edited answers no question worth asking -- and the whole point of this
    table is to be the thing you trust when a worker and the external
    platform disagree about what was submitted.

    `actor` is SET_NULL so deleting a user does not erase the history of
    what they did; the event survives, attributed to a removed account.
    """

    # System events -- the pipeline acting, not a person.
    TASK_CREATED = 'TASK_CREATED'
    TASK_ASSIGNED = 'TASK_ASSIGNED'
    TASK_UNASSIGNED = 'TASK_UNASSIGNED'
    TASK_CLAIMED = 'TASK_CLAIMED'
    TASK_RELEASED = 'TASK_RELEASED'
    TASK_STARTED = 'TASK_STARTED'
    TRANSCRIPTION_STARTED = 'TRANSCRIPTION_STARTED'
    TRANSCRIPTION_COMPLETED = 'TRANSCRIPTION_COMPLETED'
    AI_PROCESSING_STARTED = 'AI_PROCESSING_STARTED'
    AI_PROCESSING_COMPLETED = 'AI_PROCESSING_COMPLETED'
    REVIEW_STARTED = 'REVIEW_STARTED'
    READY_FOR_SUBMISSION = 'READY_FOR_SUBMISSION'
    SUBMISSION_STARTED = 'SUBMISSION_STARTED'
    SUBMISSION_COMPLETED = 'SUBMISSION_COMPLETED'
    APPROVED = 'APPROVED'
    REJECTED = 'REJECTED'
    TASK_FAILED = 'TASK_FAILED'
    TASK_CANCELLED = 'TASK_CANCELLED'
    STATUS_CHANGED = 'STATUS_CHANGED'
    # A worker pasted back a translation they polished in their own
    # assistant. Recorded so a rejection can be traced to a specific
    # version, and so "who actually did this work" has an answer.
    TRANSLATION_POLISHED = 'TRANSLATION_POLISHED'
    # A worker pasted back what their own assistant found when asked to
    # check a transcript against the accuracy-check prompt. The transcript
    # itself is unaffected by this event -- see Task.accuracy_check_notes.
    ACCURACY_CHECK_COMPLETED = 'ACCURACY_CHECK_COMPLETED'

    EVENT_CHOICES = [(name, name) for name in (
        TASK_CREATED, TASK_ASSIGNED, TASK_UNASSIGNED, TASK_CLAIMED,
        TASK_RELEASED, TASK_STARTED, TRANSLATION_POLISHED,
        ACCURACY_CHECK_COMPLETED,
        TRANSCRIPTION_STARTED,
        TRANSCRIPTION_COMPLETED, AI_PROCESSING_STARTED,
        AI_PROCESSING_COMPLETED, REVIEW_STARTED, READY_FOR_SUBMISSION,
        SUBMISSION_STARTED, SUBMISSION_COMPLETED, APPROVED, REJECTED,
        TASK_FAILED, TASK_CANCELLED, STATUS_CHANGED,
    )]

    # SEQUENTIAL PRIMARY KEY, deliberately unlike every other model here.
    #
    # Two reasons, both specific to an audit log. Ordering: several events
    # can land inside the same microsecond, and `occurred_at` alone then
    # leaves their order undefined -- a UUID tiebreak sorts them randomly,
    # so the log could show an approval before the submission it followed.
    # Tamper-evidence: a monotonic sequence makes a DELETED row visible as a
    # gap, which a UUID key cannot do.
    id = models.BigAutoField(primary_key=True)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='events')
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='task_events',
        help_text='Null for events performed by the system rather than a person.',
    )
    event_type = models.CharField(max_length=40, choices=EVENT_CHOICES)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    from_status = models.CharField(max_length=24, blank=True, default='')
    to_status = models.CharField(max_length=24, blank=True, default='')
    success = models.BooleanField(default=True)
    message = models.TextField(blank=True, default='')

    # Structured context. NEVER credentials: see `record()`, which strips
    # anything key-shaped before writing. jsonb on PostgreSQL, so this stays
    # queryable if the analytics in Phase 3 need it.
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        # `-id` first: insertion order is the truth for an append-only log,
        # and it is total, where a timestamp can tie.
        ordering = ['-id']
        indexes = [
            models.Index(fields=['task', 'occurred_at'], name='wf_event_task_time_idx'),
            models.Index(fields=['event_type', 'occurred_at'], name='wf_event_type_time_idx'),
            models.Index(fields=['actor', 'occurred_at'], name='wf_event_actor_time_idx'),
        ]

    def __str__(self):
        return f'{self.event_type} on {self.task_id} at {self.occurred_at:%Y-%m-%d %H:%M}'

    def save(self, *args, **kwargs):
        """
        Insert-only. An existing row cannot be modified.

        Enforced in code rather than only by convention, because the value
        of this table is entirely in its being unedited. A caller that
        genuinely needs to correct the record appends a new event saying so.
        """
        if self._state.adding is False:
            raise ValueError(
                'TaskEvent rows are immutable; append a new event instead of '
                'modifying an existing one.'
            )
        return super().save(*args, **kwargs)


# --------------------------------------------------------------------- #
# Invariant: a worker profile implies a workforce account
# --------------------------------------------------------------------- #

@receiver(post_save, sender=WorkerProfile)
def _ensure_account_for_worker(sender, instance, created, **kwargs):
    """
    Creating a WorkerProfile means creating a tasker. Say it once, here.

    WHY A SIGNAL RATHER THAN A LINE IN EACH CALLER. Worker profiles are
    created from the admin API, from the `create_worker` command, from test
    helpers and from fixtures. Every one of those would otherwise have to
    remember to write the role row, and the one that forgets produces a
    worker whose account says "tool" -- refused from the very queue they
    were created to work.

    AN EXISTING ROLE IS NEVER OVERWRITTEN. An administrator who also holds
    a worker code stays an administrator; this only fills in a role that is
    absent. Demotion and promotion are deliberate acts and go through the
    account API.
    """
    if not created:
        return
    Account.objects.get_or_create(
        user=instance.user,
        defaults={
            'role': roles.ADMIN if instance.user.is_staff else roles.TASKER,
        },
    )


# --------------------------------------------------------------------- #
# Idle session timeout
# --------------------------------------------------------------------- #

class TokenActivity(models.Model):
    """
    Last-seen timestamp for one DRF auth token.

    Kept as its own table rather than adding a field to
    rest_framework.authtoken.Token: that model belongs to a third-party app,
    and migrating it directly would fight DRF's own migrations on upgrade.
    A OneToOne here gets the same effect -- one row per token -- without
    touching a table this project does not own.

    WHY THIS EXISTS AT ALL. DRF's Token has no expiry concept whatsoever:
    once issued, it is valid forever until something explicitly deletes it.
    Confirmed live: a worker stayed signed in for two straight weeks with
    never a second login. See authentication.IdleTimeoutTokenAuthentication,
    which reads and updates last_used_at on every request.
    """
    token = models.OneToOneField(
        'authtoken.Token', on_delete=models.CASCADE, related_name='activity')
    last_used_at = models.DateTimeField(auto_now=True)


# --------------------------------------------------------------------- #
# External-platform credentials
# --------------------------------------------------------------------- #
#
# Defined in credentials.py and re-exported here so Django's app registry
# discovers it, keeping the encryption logic and the "never leaks toward a
# client" reasoning in one readable file rather than buried in this one.
from .credentials import EagleCredential  # noqa: E402,F401
