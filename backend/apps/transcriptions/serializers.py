from rest_framework import serializers
from .models import (
    Project, TranscriptionJob, TranscriptSegment, Translation, TranslatedSegment,
    AcademicFormat, AcademicSegment, AIQualityReport, AIRevision, AIRevisionSegment,
)

class TranscriptSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TranscriptSegment
        fields = ['id', 'speaker_label', 'start_time', 'end_time', 'text', 'confidence', 'words']

class TranslatedSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TranslatedSegment
        fields = ['id', 'speaker_label', 'start_time', 'end_time', 'text']

class TranslationSerializer(serializers.ModelSerializer):
    segments = TranslatedSegmentSerializer(many=True, read_only=True)

    class Meta:
        model = Translation
        fields = ['id', 'job', 'language', 'status', 'error_message', 'created_at', 'completed_at', 'segments', 'cancel_requested']
        read_only_fields = ['id', 'status', 'error_message', 'created_at', 'completed_at', 'cancel_requested']

class AcademicSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicSegment
        fields = ['id', 'speaker_label', 'start_time', 'end_time', 'text', 'was_modified']

class AcademicFormatSerializer(serializers.ModelSerializer):
    segments = AcademicSegmentSerializer(many=True, read_only=True)

    class Meta:
        model = AcademicFormat
        fields = [
            'id', 'job', 'subject', 'subject_confidence', 'status', 'error_message',
            'created_at', 'completed_at', 'segments', 'cancel_requested',
        ]
        read_only_fields = [
            'id', 'subject', 'subject_confidence', 'status', 'error_message',
            'created_at', 'completed_at', 'cancel_requested',
        ]

class AIRevisionSegmentSerializer(serializers.ModelSerializer):
    # The property, exposed so the frontend and the exporters agree on what a
    # segment "reads as" without either re-deriving the accept/reject rule.
    effective_text = serializers.ReadOnlyField()

    class Meta:
        model = AIRevisionSegment
        fields = [
            'id', 'speaker_label', 'start_time', 'end_time',
            'original_text', 'processed_text', 'effective_text',
            'changed', 'accepted',
        ]
        # Only `accepted` is writable, and only through the dedicated
        # accept/reject endpoints -- the texts and the timing are results, not
        # inputs. Making processed_text writable here would hand a client a
        # way to inject arbitrary text into an "AI" version, and making
        # start_time/end_time writable would break RULE 4 outright.
        read_only_fields = [
            'id', 'speaker_label', 'start_time', 'end_time',
            'original_text', 'processed_text', 'changed',
        ]


class AIRevisionSummarySerializer(serializers.ModelSerializer):
    """
    Version-switcher payload: everything needed to list revisions, and
    deliberately no segments.

    A job can accumulate several revisions of a few hundred segments each. The
    job list endpoint serializes every job it returns, so nesting segments
    here would multiply the dashboard's response size by the number of
    revisions -- and none of it is needed until the user actually opens one.
    Segments come from the detail serializer below.
    """
    # Lineage, flattened for the client: which version this one was built
    # from, and what that version is called. `source_revision_label` saves the
    # frontend a second lookup just to render "Translated ... from Corrected"
    # in the version switcher, and stays correct even after the parent is
    # deleted (source_revision is SET_NULL, but the label is copied into this
    # revision's own name at dispatch).
    source_revision_label = serializers.SerializerMethodField()

    def get_source_revision_label(self, obj):
        return obj.source_revision.label if obj.source_revision_id else None

    # The property, not a stored column: whether this version may be handed
    # to an external submission. Exposed so the export UI and the browser
    # agree on the rule without either re-deriving it -- and so "completed"
    # can never be mistaken for "approved" on the client side.
    submission_ready = serializers.ReadOnlyField()

    class Meta:
        model = AIRevision
        fields = [
            'id', 'job', 'operation', 'label', 'status', 'error_message',
            'degraded', 'source_language', 'target_language', 'model_name',
            'custom_instruction', 'segment_start_index', 'segment_end_index',
            'source_revision', 'source_revision_label',
            'prompt_tokens', 'completion_tokens', 'total_tokens',
            'cache_hits', 'cache_misses', 'estimated_tokens_saved',
            'source_text_hash', 'created_at', 'completed_at', 'cancel_requested',
            # Quality pipeline. The verdict and the counts travel with the
            # summary because the version switcher renders a badge from
            # them; the full issue list does not, for the same reason
            # segments do not -- it is only needed once a user opens the
            # revision, and nesting it here would inflate every job payload.
            'quality_profile', 'quality_status', 'quality_reason',
            'qa_iterations', 'quality_confidence', 'detected_domain',
            'detected_register', 'quality_prompt_version', 'submission_ready',
            'critical_issue_count', 'high_issue_count',
            'medium_issue_count', 'low_issue_count',
        ]
        read_only_fields = fields


class AIQualityReportSerializer(serializers.ModelSerializer):
    """
    The audit trail for one quality run.

    `report` is passed through whole. Everything in it is server-authored
    (QualityResult.to_report) and deliberately contains no prompts, no
    provider payloads and no credentials, so there is nothing here to
    filter -- see the note on the model.
    """
    class Meta:
        model = AIQualityReport
        fields = [
            'id', 'profile', 'status', 'reason', 'iterations', 'confidence',
            'model_name', 'prompt_version', 'total_tokens',
            'total_latency_seconds', 'report', 'created_at',
        ]
        read_only_fields = fields


class AIRevisionSerializer(AIRevisionSummarySerializer):
    segments = AIRevisionSegmentSerializer(many=True, read_only=True)
    segment_count = serializers.SerializerMethodField()
    changed_count = serializers.SerializerMethodField()
    # Only the newest report, not every one. A revision has at most a
    # handful, they are large, and the review panel shows the current
    # verdict -- earlier ones are history, reachable through the dedicated
    # endpoint when someone is actually investigating a regression.
    quality_report = serializers.SerializerMethodField()

    def get_segment_count(self, obj):
        return obj.segments.count()

    def get_changed_count(self, obj):
        return obj.segments.filter(changed=True).count()

    def get_quality_report(self, obj):
        latest = obj.quality_reports.first()
        return AIQualityReportSerializer(latest).data if latest else None

    class Meta(AIRevisionSummarySerializer.Meta):
        fields = AIRevisionSummarySerializer.Meta.fields + [
            'segments', 'segment_count', 'changed_count', 'quality_report',
        ]
        read_only_fields = fields


class TranscriptionJobSerializer(serializers.ModelSerializer):
    segments = TranscriptSegmentSerializer(many=True, read_only=True)
    translations = TranslationSerializer(many=True, read_only=True)
    ai_revisions = AIRevisionSummarySerializer(many=True, read_only=True)
    # SerializerMethodField, not a plain nested serializer field: academic_format
    # is a reverse OneToOneField accessor, which raises RelatedObjectDoesNotExist
    # (not just returning None) for the vast majority of jobs -- every job that
    # hasn't had academic formatting triggered yet, which is every existing job
    # at the moment this feature ships. A plain nested field would crash the
    # whole job list/detail endpoint for those; this returns null instead.
    academic_format = serializers.SerializerMethodField()

    def get_academic_format(self, obj):
        academic_format = getattr(obj, 'academic_format', None)
        return AcademicFormatSerializer(academic_format).data if academic_format else None

    def validate_source_url(self, value):
        """
        THE SECOND WAY A URL REACHES THE FETCHER.

        The discovery endpoint is guarded at the view, but a job created
        with input_source='url' is fetched too (tasks.py runs the download
        against job.source_url), and it arrives through this serializer.
        Guarding one door and not the other leaves the same server-side
        request forgery reachable by a slightly different request -- which
        is exactly how the original check ended up protecting nothing.
        """
        from core.services.url_guard import UnsafeURL, check_url

        if not value:
            return value
        try:
            return check_url(value)
        except UnsafeURL as exc:
            raise serializers.ValidationError(str(exc))

    class Meta:
        model = TranscriptionJob
        fields = [
            'id', 'project', 'input_source', 'source_url',
            'file_path', 'status', 'language', 'detected_language',
            'language_confidence', 'error_message', 'created_at',
            'completed_at', 'segments', 'translations', 'academic_format',
            'series_id', 'series_index', 'series_title', 'cancel_requested',
            'enable_diarization', 'speaker_aliases', 'ai_revisions',
            'document_metadata',
        ]
        # file_path is deliberately read-only from the API's perspective: it
        # must only ever be set server-side (see
        # TranscriptionJobViewSet.create(), which passes it as an explicit
        # save() kwarg after actually writing the uploaded file to disk --
        # that still works with this field read-only, since save(**kwargs)
        # bypasses validated_data entirely). Without this, a client could
        # POST input_source='upload' with an arbitrary file_path directly
        # and have the pipeline run ffmpeg against any server-readable path.
        #
        # speaker_aliases is also read-only here for a similar reason: it's
        # only ever mutated through the dedicated set_speaker_aliases action
        # below (see views.py), never as a side effect of an unrelated
        # PATCH/PUT to the job itself -- same pattern as cancel_requested.
        #
        # document_metadata follows exactly that precedent: written only via
        # the dedicated metadata action, which whitelists the keys it accepts.
        # Left writable here, a client could store an arbitrary blob of any
        # size or shape on the job.
        read_only_fields = [
            'id', 'status', 'file_path', 'detected_language', 'language_confidence',
            'error_message', 'created_at', 'completed_at', 'cancel_requested',
            'speaker_aliases', 'document_metadata',
        ]

class ProjectSerializer(serializers.ModelSerializer):
    jobs = TranscriptionJobSerializer(many=True, read_only=True)

    class Meta:
        model = Project
        fields = ['id', 'name', 'description', 'created_at', 'updated_at', 'jobs']
