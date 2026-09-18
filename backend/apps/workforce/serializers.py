"""
Wire formats for the workforce API.

TWO SERIALIZERS PER MODEL WHERE THE AUDIENCE DIFFERS. A worker and an admin
are shown different fields for the same Task, and that is enforced by
choosing the serializer, not by deleting keys afterwards -- a filter applied
after the fact is one forgotten branch away from leaking.

NOTHING WRITABLE THAT DRIVES STATE. `status`, `claimed_by` and every
timestamp are read-only everywhere. State changes happen through the
dedicated action endpoints, which go through the service layer, which holds
the row lock and writes the audit event. A writable `status` field would be
a way around all three.
"""
from django.contrib.auth.models import User
from rest_framework import serializers

from . import roles, states
from .models import Account, Task, TaskEvent, WorkerProfile


class WorkerProfileSerializer(serializers.ModelSerializer):
    """What a worker sees about themselves."""

    username = serializers.CharField(source='user.username', read_only=True)
    is_admin = serializers.BooleanField(source='user.is_staff', read_only=True)
    active = serializers.BooleanField(source='is_active_worker', read_only=True)
    held_task_count = serializers.SerializerMethodField()

    class Meta:
        model = WorkerProfile
        fields = [
            'id', 'worker_code', 'display_name', 'username', 'status',
            'active', 'is_admin', 'max_concurrent_tasks', 'held_task_count',
            'last_activity_at', 'created_at',
        ]
        read_only_fields = fields

    def get_held_task_count(self, obj):
        return Task.objects.filter(
            claimed_by=obj, status__in=states.WORKER_HELD_STATES
        ).count()


class AdminWorkerSerializer(serializers.ModelSerializer):
    """
    Admin view of a worker, plus the counters an operator actually asks for.

    Deliberately still exposes no personal data beyond the display name --
    an admin having more authority does not mean the system should be
    storing more about people.
    """

    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    user_is_active = serializers.BooleanField(source='user.is_active', read_only=True)
    is_admin = serializers.BooleanField(source='user.is_staff', read_only=True)
    active = serializers.BooleanField(source='is_active_worker', read_only=True)

    class Meta:
        model = WorkerProfile
        fields = [
            'id', 'worker_code', 'display_name', 'username', 'email',
            'status', 'active', 'user_is_active', 'is_admin',
            'max_concurrent_tasks', 'last_activity_at', 'created_at', 'updated_at',
        ]
        # Only the operational knobs are writable. Username, email and staff
        # status are account concerns, changed through account management
        # rather than as a side effect of editing a worker profile.
        read_only_fields = [
            'id', 'worker_code', 'username', 'email', 'active',
            'user_is_active', 'is_admin', 'last_activity_at',
            'created_at', 'updated_at',
        ]


class WorkerCreateSerializer(serializers.Serializer):
    """
    Admin-only worker creation: a Django user and its profile together.

    The password is write-only and validated by Django's configured
    validators, which Phase 2 turned on (they were an empty list). Creating
    a worker with password "1234" is now rejected at the serializer.
    """

    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=12)
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    worker_code = serializers.CharField(max_length=32)
    display_name = serializers.CharField(max_length=120, required=False, allow_blank=True, default='')
    max_concurrent_tasks = serializers.IntegerField(required=False, min_value=1, max_value=20, default=3)

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError('That username is already taken.')
        return value

    def validate_worker_code(self, value):
        if WorkerProfile.objects.filter(worker_code__iexact=value).exists():
            raise serializers.ValidationError('That worker code is already in use.')
        return value

    def validate_password(self, value):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value


class TaskSerializer(serializers.ModelSerializer):
    """
    The worker-facing task.

    Omits `notes` and the internal failure text: a worker needs to know a
    task failed and what to do next, not the operator's annotations.
    """

    claimed_by_code = serializers.CharField(source='claimed_by.worker_code', read_only=True, default=None)
    assigned_to_code = serializers.CharField(source='assigned_to.worker_code', read_only=True, default=None)
    is_claimable = serializers.BooleanField(read_only=True)
    available_transitions = serializers.SerializerMethodField()
    # Whether the machine work is already done. A tasker picking from the
    # queue should be able to see this before they commit: claiming a cold
    # task starts the external one-hour clock on work that has not begun.
    is_ready = serializers.SerializerMethodField()
    # Which verbatim standard this transcript must follow, and whether we
    # actually know or merely assumed. See get_transcription_standard.
    transcription_standard = serializers.SerializerMethodField()

    class Meta:
        model = Task
        # `source_url` IS DELIBERATELY ABSENT. It is a live Eagle URL, and a
        # tasker has no use for it: our own endpoint serves the audio and our
        # own submitter sends the work back. Putting it on the wire would
        # invite someone to open Eagle directly, which is the one thing this
        # architecture exists to make unnecessary. Admins get it on
        # AdminTaskSerializer, where it is a diagnostic.
        fields = [
            'id', 'external_task_id', 'external_source',
            'kind', 'title', 'project', 'transcription_standard',
            'source_language', 'target_language',
            'duration_seconds', 'status', 'priority',
            'assigned_to', 'assigned_to_code', 'claimed_by', 'claimed_by_code',
            'job', 'deliverable_revision', 'is_claimable', 'is_ready',
            'available_transitions', 'rejection_reason', 'failure_reason',
            'submitted_content',
            'deadline_at',
            'created_at', 'assigned_at', 'claimed_at', 'started_at',
            'review_started_at', 'ready_at', 'submitted_at',
            'approved_at', 'rejected_at', 'failed_at', 'updated_at',
        ]
        # EVERYTHING is read-only. State moves through the action endpoints
        # so that the lock and the audit event cannot be bypassed.
        read_only_fields = fields

    def get_is_ready(self, obj):
        """
        True when a completed transcript is attached -- and, for a
        translation task, a translation as well. Anything less means the
        worker would be waiting on the machine with the clock running.
        """
        if not obj.job_id or getattr(obj.job, 'status', None) != 'completed':
            return False
        if obj.kind == Task.TRANSLATION:
            return obj.deliverable_revision_id is not None
        return True

    def get_transcription_standard(self, obj):
        """
        The standard this transcript is graded under -- and how sure we are.

        WHY `assumed` IS A SEPARATE FLAG AND NOT AN IMPLEMENTATION DETAIL.
        The two standards are opposites: one strips fillers and expands
        contractions, the other preserves both exactly. Applying the wrong
        one does not produce a slightly worse document, it produces a
        document that is wrong in every line, and no amount of care in the
        words rescues it.

        Eagle assigns the standard per task via the project label, and until
        that label reaches us we are guessing from a default. A guess shown
        as a fact is the dangerous version, so the interface is told which it
        is and can say so where a person will see it before submitting.
        """
        if obj.kind != Task.TRANSCRIPTION:
            return None
        from django.conf import settings
        from core.services.ai_assist.quality import get_standard, standard_for_project

        project = (obj.project or '').strip()
        fallback = getattr(settings, 'EAGLE_TRANSCRIPTION_STANDARD', 'edited_verbatim')
        name = standard_for_project(project, fallback)
        standard = get_standard(name)
        return {
            'name': name,
            'label': standard.label if standard else name,
            'assumed': not project,
        }

    def get_available_transitions(self, obj):
        """
        What THIS caller may do next, so the UI can render exactly the
        buttons the server will accept rather than guessing and showing
        actions that 403.
        """
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        from .services import role_of
        return sorted(states.allowed_transitions(obj.status, role_of(user)))


class AdminTaskSerializer(TaskSerializer):
    """Admin view: adds the operational annotations."""

    class Meta(TaskSerializer.Meta):
        fields = TaskSerializer.Meta.fields + [
            'notes', 'cancelled_at',
            # Admin-only: the operator needs to be able to open the task on
            # Eagle when something looks wrong.
            'source_url',
        ]
        read_only_fields = fields


class TaskCreateSerializer(serializers.ModelSerializer):
    """
    Admin-only intake.

    `status` is absent by design: a task is always created in NEW and moved
    from there through the machine.
    """

    class Meta:
        model = Task
        fields = [
            'external_task_id', 'external_source', 'source_url', 'kind',
            'title', 'source_language', 'target_language',
            'duration_seconds', 'priority', 'notes',
        ]

    def validate(self, attrs):
        if attrs.get('kind') == Task.TRANSLATION and not attrs.get('target_language'):
            raise serializers.ValidationError(
                {'target_language': 'A translation task needs a target language.'}
            )
        return attrs


class TaskEventSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source='actor.username', read_only=True, default=None)

    class Meta:
        model = TaskEvent
        fields = [
            'id', 'task', 'event_type', 'actor', 'actor_username',
            'occurred_at', 'from_status', 'to_status', 'success',
            'message', 'metadata',
        ]
        read_only_fields = fields


class AccountSerializer(serializers.ModelSerializer):
    """
    An account and what kind it is. Admin-facing.

    Carries the worker code when there is one, so the account list can show
    "who is a tasker and which code are they" without a second request --
    but the worker fields stay on WorkerProfile, because a tool user has
    none of them.
    """

    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    user_is_active = serializers.BooleanField(source='user.is_active', read_only=True)
    is_staff = serializers.BooleanField(source='user.is_staff', read_only=True)
    role_label = serializers.SerializerMethodField()
    capabilities = serializers.SerializerMethodField()
    worker_code = serializers.SerializerMethodField()
    worker_status = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = [
            'id', 'username', 'email', 'role', 'role_label', 'capabilities',
            'user_is_active', 'is_staff', 'worker_code', 'worker_status',
            'created_at', 'updated_at',
        ]
        # `role` is changed through the dedicated action, not by PATCH, for
        # the same reason task status is: the action enforces the rules
        # about held work and self-demotion that a bare field write cannot.
        read_only_fields = fields

    def get_role_label(self, obj):
        return roles.label(obj.role)

    def get_capabilities(self, obj):
        return sorted(roles.capabilities(obj.role))

    def _profile(self, obj):
        return WorkerProfile.objects.filter(user=obj.user).first()

    def get_worker_code(self, obj):
        profile = self._profile(obj)
        return profile.worker_code if profile else None

    def get_worker_status(self, obj):
        profile = self._profile(obj)
        return profile.status if profile else None


class AccountCreateSerializer(serializers.Serializer):
    """
    Admin-only account creation, for any role.

    A tasker needs a worker code and gets a WorkerProfile; a tool user
    needs neither and must not be given an empty one. The role decides,
    which is why this validates conditionally rather than demanding worker
    fields from everyone.
    """

    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=12)
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    role = serializers.ChoiceField(choices=roles.ROLE_CHOICES, default=roles.TOOL)

    # Tasker-only fields.
    worker_code = serializers.CharField(max_length=32, required=False, allow_blank=True, default='')
    display_name = serializers.CharField(max_length=120, required=False, allow_blank=True, default='')
    max_concurrent_tasks = serializers.IntegerField(required=False, min_value=1, max_value=20, default=3)

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError('That username is already taken.')
        return value

    def validate_password(self, value):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def validate(self, attrs):
        if attrs.get('role') == roles.TASKER:
            code = (attrs.get('worker_code') or '').strip()
            if not code:
                raise serializers.ValidationError(
                    {'worker_code': 'A tasker needs a worker code.'}
                )
            if WorkerProfile.objects.filter(worker_code__iexact=code).exists():
                raise serializers.ValidationError(
                    {'worker_code': 'That worker code is already in use.'}
                )
        return attrs
