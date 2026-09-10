from django.conf import settings
from django.db import models
import uuid

class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class TranscriptionJob(models.Model):
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('downloading', 'Downloading'),
        ('transcribing', 'Transcribing'),
        ('diarizing', 'Diarizing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    SOURCE_CHOICES = [
        ('upload', 'File Upload'),
        ('url', 'Public Media URL'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='jobs')

    # Who created this job. Nullable because 224 rows predate the concept,
    # and SET_NULL because deleting a person must never delete transcript
    # work -- the same rule AIRevision.owner already follows.
    #
    # THIS FIELD IS THE AUTHORIZATION BOUNDARY. Before it existed the whole
    # transcription API ran under AllowAny with no ownership at all: an
    # anonymous caller could read every job, every segment and every Eagle
    # source URL. `permissions.visible_jobs_for` is now the single place
    # that decides who sees what, and it keys off this.
    #
    # A NULL owner is visible to administrators only. That is deliberate:
    # treating "no owner" as "everyone" would reinstate exactly the leak
    # this closes, and the pre-existing rows genuinely have no owner to
    # attribute them to.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='transcription_jobs',
        db_index=True,
    )

    input_source = models.CharField(max_length=50, choices=SOURCE_CHOICES)
    source_url = models.URLField(blank=True, null=True, max_length=1000)
    file_path = models.CharField(max_length=1000, blank=True, null=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='queued')
    # User-selected language code (e.g. 'sw', 'fr'); null/blank means Auto Detect.
    language = models.CharField(max_length=10, blank=True, null=True)
    detected_language = models.CharField(max_length=10, blank=True, null=True)
    language_confidence = models.FloatField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    # Optional multi-page/series grouping (Phase 5). series_id is shared by
    # every job created from the same discovered playlist/series/next-page
    # chain; null means this job is standalone, exactly as before this phase.
    series_id = models.UUIDField(blank=True, null=True, db_index=True)
    series_index = models.PositiveIntegerField(blank=True, null=True)
    series_title = models.CharField(max_length=255, blank=True, null=True)

    # Optional per-job auth the user explicitly pasted for this source URL
    # (see core.services.auth_helper.parse_auth_input) -- a flat dict of
    # extra HTTP headers (typically just Cookie) sent with every request the
    # extraction pipeline makes for this job. Null means unauthenticated,
    # exactly as before this field existed. Write-only from the API's
    # perspective (never included in TranscriptionJobSerializer's output)
    # since it may contain live session credentials.
    site_auth_headers = models.JSONField(blank=True, null=True)

    # Cooperative cancellation flag (Part 2). Set True by the cancel
    # endpoint; the running background thread polls this at stage
    # boundaries (and, during the yt-dlp download specifically, every
    # ~0.5s while the subprocess is alive) and stops itself -- nothing
    # ever force-kills the background thread directly.
    cancel_requested = models.BooleanField(default=False)

    # User opt-in for real speaker diarization (see
    # core.services.diarization.SpeakerDiarizer). Off by default: it's an
    # extra whole-file pass on top of transcription, so jobs that don't
    # need it pay zero additional cost. When True but the diarization
    # library/model isn't available on this machine, the pipeline falls
    # back to the same placeholder alternation used before this feature
    # existed -- this flag being True never causes a job to fail.
    enable_diarization = models.BooleanField(default=False)

    # Optional hint for HOW MANY speakers diarization should look for.
    # NULL (the default) means fully automatic -- clustering decides the
    # count itself via DIARIZATION_CLUSTER_THRESHOLD, exactly as before
    # this field existed. When set, it's passed straight to
    # AgglomerativeClustering as n_clusters, which overrides the
    # distance-threshold auto-detection entirely for that job.
    #
    # WHY THIS EXISTS. Automatic clustering can split one speaker into two
    # or three on a tone shift alone -- DIARIZATION_MIN_SPEAKER_SECONDS
    # already guards the worst of this (see diarization.py), but that
    # guard is duration-based and cannot rescue a split that persists long
    # enough to look like a real speaker. A human who already knows the
    # true count (a 1-on-1 interview, a known panel size) can rule that
    # class of error out entirely for their own job, the same tradeoff
    # TurboScribe's own "specify speakers" option makes -- automatic
    # detection stays the default and is never removed, this only adds an
    # opt-in override on top of it.
    #
    # PositiveSmallIntegerField, not a plain int: a value must be >= 1 if
    # given at all (0 or negative speakers is meaningless), enforced at
    # the serializer layer where the user-facing error belongs.
    expected_speakers = models.PositiveSmallIntegerField(null=True, blank=True)

    # User-editable display names for this job's speaker labels, e.g.
    # {"Speaker 0": "Interviewer", "Speaker 1": "Doctor"}. Deliberately a
    # flat dict keyed by the underlying speaker_label stored on every
    # TranscriptSegment/TranslatedSegment -- renaming never touches those
    # rows, it only changes what's displayed/exported, so it's instant,
    # reversible (removing a key or clearing the dict reverts to the raw
    # label), and never risks desyncing from the actual diarization/
    # placeholder data underneath.
    speaker_aliases = models.JSONField(default=dict, blank=True)

    # Editable document-level facts about the recording -- title, presenter,
    # date, location, subject, tags, notes -- for the AI Assist pre-flight
    # panel and for export headers, which until now had no title to use and
    # hardcoded "Transcript Report - Job <uuid>" (ExportTranscriptView).
    #
    # A JSONField following the speaker_aliases precedent above rather than a
    # column per fact, for the same reason: these are display metadata, not
    # pipeline inputs. Nothing in transcription reads them, so adding a field
    # later is a frontend change and a serializer whitelist entry rather than
    # a migration. Keys are whitelisted server-side at the write endpoint --
    # the blob is never trusted to be arbitrary.
    document_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['series_id', 'series_index', '-created_at']

    def __str__(self):
        return f"{self.id} - {self.status}"

class DiscoveryTask(models.Model):
    """
    Tracks one background run of series_discovery.discover(url) so the HTTP
    request that kicks it off can return immediately instead of blocking for
    however long the discovery strategies take. The frontend polls this by
    id until status is completed/failed, then shows the exact same
    series-choice UI it always has -- only the transport changed.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_url = models.URLField(max_length=1000)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    result = models.JSONField(blank=True, null=True)
    error_message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    # Same per-job auth mechanism as TranscriptionJob.site_auth_headers --
    # discovery needs it too, since a login-walled page shows only its
    # login form without it (see core.services.auth_helper).
    site_auth_headers = models.JSONField(blank=True, null=True)

    # Explicit opt-in for SequentialQueryParamStrategy (see
    # core.services.discovery.sequential_param_strategy) -- off by default
    # since walking a numbered-sequence query parameter can have real side
    # effects on the source site, unlike every other discovery strategy.
    try_sequential = models.BooleanField(default=False)

    def __str__(self):
        return f"DiscoveryTask({self.id}) - {self.status}"


class TranscriptSegment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(TranscriptionJob, on_delete=models.CASCADE, related_name='segments')
    # Blank ('') means "no speaker identity" -- the correct, expected value
    # whenever the job's enable_diarization is False, or diarization ran but
    # produced no turn overlapping this segment. Never a fabricated label
    # (see run_transcription_pipeline in tasks.py) -- only ever a real
    # diarization-assigned label, or empty.
    speaker_label = models.CharField(max_length=100, default='', blank=True)
    start_time = models.FloatField()  # seconds
    end_time = models.FloatField()    # seconds
    text = models.TextField()
    # Null means "never edited -- `text` IS the original ASR output."
    # Populated once, on the FIRST edit (see EditSegmentView), and never
    # overwritten by a later edit: a worker who corrects a line twice can
    # still get back to the true original, not just their previous edit.
    # Cleared back to null by reverting, so "has this ever been touched" and
    # "is there something to revert to" stay the same question.
    original_text = models.TextField(blank=True, null=True)
    confidence = models.FloatField(blank=True, null=True)
    words = models.JSONField(blank=True, null=True)  # Detailed word timings
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_time']
        indexes = [
            models.Index(fields=['job', 'start_time']),
        ]

    def __str__(self):
        return f"[{self.start_time:.2f}-{self.end_time:.2f}] {self.speaker_label}: {self.text[:30]}"


class Translation(models.Model):
    """
    A translated version of a job's transcript into one target language.
    Deliberately separate from TranscriptionJob/TranscriptSegment: the
    original transcript is never modified or overwritten by translating it.
    One job can have many Translations (one per target language), each
    created only when a user explicitly requests it -- never automatically.
    """
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('translating', 'Translating'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(TranscriptionJob, on_delete=models.CASCADE, related_name='translations')
    language = models.CharField(max_length=10)  # target language code
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='queued')
    error_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    cancel_requested = models.BooleanField(default=False)

    class Meta:
        ordering = ['language']
        # Caching: a job can only have one translation per target language --
        # requesting an already-completed language returns the existing one
        # instantly instead of re-translating.
        constraints = [
            models.UniqueConstraint(fields=['job', 'language'], name='unique_job_translation_language'),
        ]

    def __str__(self):
        return f"{self.job_id} -> {self.language} ({self.status})"


class TranslatedSegment(models.Model):
    """
    One translated segment. Mirrors its original TranscriptSegment 1:1:
    same start/end time and speaker, copied at creation time and never
    recalculated -- translation changes only the text, never the timing.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    translation = models.ForeignKey(Translation, on_delete=models.CASCADE, related_name='segments')
    original_segment = models.ForeignKey(TranscriptSegment, on_delete=models.CASCADE, related_name='translated_segments')
    speaker_label = models.CharField(max_length=100, default='', blank=True)
    start_time = models.FloatField()
    end_time = models.FloatField()
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_time']
        indexes = [
            models.Index(fields=['translation', 'start_time']),
        ]

    def __str__(self):
        return f"[{self.start_time:.2f}-{self.end_time:.2f}] {self.speaker_label}: {self.text[:30]}"


class AcademicFormat(models.Model):
    """
    An optional, on-demand notation-conversion pass over a job's completed
    transcript -- "x squared" -> "x²", "H two O" -> "H₂O", etc. Deliberately
    the same architectural shape as Translation (separate model, separate
    background task, cached, cancellable, never mutates the original
    transcript), with two differences from Translation, both load-bearing:

    1. OneToOneField, not ForeignKey+UniqueConstraint: unlike a translation
       target language, there's no user-chosen dimension here -- subject is
       *detected*, not picked -- so there is exactly one AcademicFormat per
       job, ever, and get_or_create(job=job) is naturally "cache by job".
    2. Caching is content-hash-based (source_text_hash), not just "does a
       row already exist": segment text can be edited after the fact (see
       EditSegmentView), so existence alone isn't a valid cache signal --
       only "the segments this was generated from are still exactly the
       segments the job has now" is.
    """
    SUBJECT_CHOICES = [
        ('GENERAL', 'General'),
        ('MATHEMATICS', 'Mathematics'),
        ('PHYSICS', 'Physics'),
        ('CHEMISTRY', 'Chemistry'),
        ('STEM_MIXED', 'STEM Mixed'),
    ]
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('detecting', 'Detecting Subject'),
        ('formatting', 'Formatting'),
        ('validating', 'Validating'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.OneToOneField(TranscriptionJob, on_delete=models.CASCADE, related_name='academic_format')
    # Blank until the 'detecting' stage actually runs.
    subject = models.CharField(max_length=20, choices=SUBJECT_CHOICES, blank=True)
    subject_confidence = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    error_message = models.TextField(blank=True, null=True)
    # sha256 over the ordered (segment.id, segment.text) pairs this was
    # generated from -- see core.services.academic.compute_source_text_hash.
    # The sole cache-invalidation signal: a later request with a matching
    # hash returns this row's segments unchanged; a mismatch (segments were
    # edited since) triggers a real regeneration, exactly like Translation
    # already treats a 'failed' row as never cached forever.
    source_text_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    cancel_requested = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.job_id} -> {self.subject or '?'} ({self.status})"


class AcademicSegment(models.Model):
    """
    One notation-converted segment. Mirrors its original TranscriptSegment
    1:1 -- same start/end time and speaker, copied at generation time and
    never recalculated -- formatting changes only the text, never the
    timing, exactly like TranslatedSegment. `text` is always plain Unicode
    (produced directly by the formatter's own substitution rules, never
    LaTeX -- see core.services.academic.mathematics/physics/chemistry), so
    every export format can use it exactly as-is with zero extra
    conversion. `was_modified` is False whenever the formatter found
    nothing to convert in this segment (ordinary prose) or the validator
    rejected an attempted conversion and fell back to the original text --
    both cases leave `text` identical to the source segment's own text.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    academic_format = models.ForeignKey(AcademicFormat, on_delete=models.CASCADE, related_name='segments')
    original_segment = models.ForeignKey(TranscriptSegment, on_delete=models.CASCADE, related_name='academic_segments')
    speaker_label = models.CharField(max_length=100, default='', blank=True)
    start_time = models.FloatField()
    end_time = models.FloatField()
    text = models.TextField()
    was_modified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_time']
        indexes = [
            models.Index(fields=['academic_format', 'start_time']),
        ]

    def __str__(self):
        return f"[{self.start_time:.2f}-{self.end_time:.2f}] {self.speaker_label}: {self.text[:30]}"


class AIRevision(models.Model):
    """
    One AI post-processing pass over some or all of a job's transcript --
    the version record behind the AI Assist workspace.

    Architecturally the same shape as AcademicFormat and Translation
    (separate model, background thread, content-hash cache, cancel flag, WS
    broadcasts) and for the same reason: the original TranscriptSegment rows
    stay the single source of truth and are never written to by this
    feature. What differs from those two:

    1. ForeignKey, not OneToOne. Unlike AcademicFormat there IS a
       user-chosen dimension here -- several, in fact (operation, target
       language, range, free-text instruction) -- so a job accumulates many
       revisions and the user picks between them, exactly as they pick
       between translation languages.
    2. The cache key is request_hash, not just source_text_hash. Two
       requests over the same unchanged text are still different requests if
       the operation, language, model, or instruction differs, so all of
       those go into the digest (see ai_assist.compute_request_hash).
       source_text_hash is kept alongside it to answer a separate question:
       whether this revision is stale relative to the job's current
       segments.
    3. Token counts are recorded. Provider calls cost money and free-tier
       quota, so every run's usage lands both here and in the append-only
       AIUsageRecord ledger that the budget check reads.

    `segment_start_index` / `segment_end_index` implement the user-selected
    range ("start from here to the end of the transcript"). They are
    positions in the job's start_time-ordered segment list, interpreted as a
    Python slice -- start inclusive, end EXCLUSIVE, and end=None meaning
    "through the last segment". Indices rather than segment UUIDs because
    the UI selects a contiguous span by position; see source_segments().
    """
    OPERATION_CHOICES = [
        ('correct', 'Correct Transcription Errors'),
        ('academic', 'Academic Notation'),
        ('translate', 'Translate'),
        ('improve', 'Improve Wording'),
        ('custom', 'Custom Instruction'),
    ]
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(TranscriptionJob, on_delete=models.CASCADE, related_name='ai_revisions')

    # The authentication seam. This app has no auth today (settings.py:
    # AllowAny with empty DEFAULT_AUTHENTICATION_CLASSES), so this is always
    # null and nothing reads it. It exists now, nullable, so that adding real
    # accounts later is a permission-class change plus a backfill rather than
    # a migration across every AI table. SET_NULL: deleting a user must not
    # delete transcript work.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='ai_revisions',
    )

    operation = models.CharField(max_length=20, choices=OPERATION_CHOICES)
    # Display name in the version switcher, e.g. "Academic" or "Swahili".
    # Derived server-side from the operation and target language.
    label = models.CharField(max_length=120, blank=True)
    # Only meaningful for operation='custom'. Stored so the revision is
    # reproducible and so its hash can distinguish two custom runs.
    custom_instruction = models.TextField(blank=True, default='')
    source_language = models.CharField(max_length=10, blank=True, null=True)
    target_language = models.CharField(max_length=10, blank=True, null=True)
    # Resolved at dispatch time, not read from config at display time: the
    # configured model can change, and a revision must keep reporting which
    # model actually produced it.
    model_name = models.CharField(max_length=100, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    # Client-visible, so only ever an AIAssistError.safe_message -- never
    # str(exception), which for a provider failure can carry the request URL,
    # quota internals, or echoed prompt text.
    error_message = models.TextField(blank=True, null=True)
    # True when at least one batch failed validation twice and kept its
    # original text (see AIPostProcessor's degrade policy). Surfaced so the
    # UI can say "some segments could not be processed" rather than letting
    # an unchanged span look like a clean transcript.
    degraded = models.BooleanField(default=False)

    segment_start_index = models.PositiveIntegerField(default=0)
    segment_end_index = models.PositiveIntegerField(blank=True, null=True)

    # Which version this revision READS FROM. Null means the original
    # transcript; set means "run this operation over that version's text".
    #
    # This exists because the useful requests are chained ones: correct the
    # ASR errors first, then translate the corrected text -- translating raw
    # ASR output and then hoping for the best is measurably worse. Without
    # this field every operation would fork off the original and the two
    # results could only be compared, never composed.
    #
    # It does not weaken RULE 3 or RULE 4. A chain still never writes to
    # TranscriptSegment, and every link copies start_time/end_time/
    # speaker_label from the SAME root transcript row (see source_segments),
    # so timing and diarization are inherited from the original rather than
    # from the previous link -- a chain of ten operations still lines up
    # exactly with the audio.
    #
    # SET_NULL rather than CASCADE: a derived revision's text is already
    # materialized in its own segments, so deleting the version it came from
    # must not delete it too. It only loses the record of where it came from.
    source_revision = models.ForeignKey(
        'self', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='derived_revisions',
    )

    # No db_index=True here: every lookup is job-scoped ("does THIS job
    # already have this exact request?"), which the composite index below
    # serves. A second standalone index on the column would be dead weight.
    request_hash = models.CharField(max_length=64, blank=True)
    source_text_hash = models.CharField(max_length=64, blank=True)

    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    cache_hits = models.PositiveIntegerField(default=0)
    cache_misses = models.PositiveIntegerField(default=0)
    estimated_tokens_saved = models.PositiveIntegerField(default=0)

    # ------------------------------------------------------------------ #
    # Quality pipeline
    # ------------------------------------------------------------------ #
    #
    # A SECOND, INDEPENDENT AXIS FROM `status`. `status` answers "did the run
    # finish?"; `quality_status` answers "is the text trustworthy?". They are
    # separate columns because conflating them is the bug that let a rejected
    # translation ship: status='completed' meant only that Gemini returned
    # well-formed text, and it was read as "ready to submit".
    #
    # A revision can therefore be status='completed' AND
    # quality_status='quality_review_required' at the same time, which is
    # exactly the state a reviewer needs to see. Existing rows and every run
    # without a profile stay at 'not_run', so nothing about the old behaviour
    # or the old UI changes.
    # 'pending' and 'auditing' exist so that "the gate has not reported yet"
    # is distinguishable from "there is no gate on this run". Without them
    # both read as 'not_run', which means a worker that dies mid-audit leaves
    # a revision permanently claiming it was never gated -- a lie that gets
    # more convincing the longer it sits there.
    QUALITY_STATUS_CHOICES = [
        ('not_run', 'No quality pipeline'),
        ('pending', 'Quality Pipeline Queued'),
        ('auditing', 'Auditing'),
        ('validated', 'Validated'),
        ('quality_review_required', 'Quality Review Required'),
        ('technical_failure', 'Technical Failure'),
    ]

    # The states that mean "the gate ran and said no". Distinct from the
    # in-progress and never-ran states, because only these justify refusing
    # a submission export -- refusing 'not_run' would block every ungated
    # revision, which is every revision by default.
    QUALITY_REFUSED_STATUSES = ('quality_review_required', 'technical_failure')
    QUALITY_IN_PROGRESS_STATUSES = ('pending', 'auditing')

    # Which profile was requested. Stored rather than re-derived at display
    # time for the same reason model_name is: the configured default can
    # change, and a revision must keep reporting the gate it actually passed.
    quality_profile = models.CharField(max_length=30, blank=True, default='')
    # Eagle transcription standard this revision was produced under
    # ('edited_verbatim' / 'strict_verbatim'), set only for the warm
    # standard pass. The gate reads it to check the transcript-rubric
    # polarity -- fillers kept or removed, contractions kept or expanded --
    # rather than the translation checks, which are the wrong rulebook for
    # a same-language pass. Stored, not re-derived: the project->standard
    # mapping can change and this revision must keep reporting the standard
    # it was actually graded against.
    transcript_standard = models.CharField(max_length=30, blank=True, default='')
    quality_status = models.CharField(
        max_length=30, choices=QUALITY_STATUS_CHOICES, default='not_run',
    )
    # Operator-facing explanation of a refusal. Client-visible, so it is only
    # ever the gate's own summary (issue counts) or an AIAssistError
    # .safe_message -- never a provider body, same rule as error_message.
    quality_reason = models.TextField(blank=True, default='')
    qa_iterations = models.PositiveIntegerField(default=0)
    quality_confidence = models.FloatField(default=0.0)
    # Denormalized from the report's issue list so the version switcher can
    # show a badge, and so "find every revision still carrying a critical
    # finding" is an indexed query rather than a JSON scan over every row.
    critical_issue_count = models.PositiveIntegerField(default=0)
    high_issue_count = models.PositiveIntegerField(default=0)
    medium_issue_count = models.PositiveIntegerField(default=0)
    low_issue_count = models.PositiveIntegerField(default=0)
    # What the source turned out to be. Denormalized off the report for the
    # same display reason, and because "how does this platform's traffic
    # break down by domain?" is a question worth being able to answer.
    detected_domain = models.CharField(max_length=80, blank=True, default='')
    detected_register = models.CharField(max_length=80, blank=True, default='')
    quality_prompt_version = models.CharField(max_length=20, blank=True, default='')

    cancel_requested = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']
        # Named explicitly rather than letting Django hash a name, so the
        # hand-written migration for these models is exact.
        indexes = [
            models.Index(fields=['job', 'status'], name='ai_rev_job_status_idx'),
            models.Index(fields=['job', 'request_hash'], name='ai_rev_job_reqhash_idx'),
            models.Index(fields=['job', 'quality_status'], name='ai_rev_job_qstatus_idx'),
        ]

    @property
    def submission_ready(self) -> bool:
        """
        Whether this version may be handed to an external submission.

        Deliberately requires quality_status=='validated' rather than
        accepting 'not_run'. A revision that never went through the gate is
        not thereby approved by it -- spec 34's whole point is that reaching
        a ready state must be an explicit software decision, not the default
        that follows from nothing having objected.
        """
        return (
            self.status == 'completed'
            and self.quality_status == 'validated'
            and not self.degraded
        )

    def __str__(self):
        return f"{self.job_id} -> {self.operation} ({self.status})"

    def source_segments(self):
        """
        The segments this revision covers, in transcript order.

        Reads from `source_revision` when one is set and from the job's own
        transcript otherwise, so a chained run ("translate the corrected
        version") and a direct one are the same code path to every caller --
        the pipeline, the hasher and the budget estimator all just iterate
        this.

        Returns a list rather than a queryset because the slice bounds are
        Python indices, and because callers iterate it more than once
        (hashing, then batching).
        """
        segments = self._base_segments()
        end = self.segment_end_index if self.segment_end_index is not None else len(segments)
        return segments[self.segment_start_index:end]

    def _base_segments(self):
        """
        The unsliced list this revision's range applies to.

        For a chained revision, each upstream row is presented as its ROOT
        TranscriptSegment carrying the upstream *effective* text. That is
        deliberate on three counts:

          * Timing and speaker come from the original row, so no amount of
            chaining can drift them (RULE 4). Reading them off the previous
            link would work too -- it copied them faithfully -- but only
            because it did, and this way it cannot matter.
          * The returned objects are real TranscriptSegment instances, so
            AIRevisionSegment.original_segment still points at the root
            transcript row no matter how long the chain is. Lineage stays a
            star around the original, not a linked list that has to be walked
            to find out what was actually said.
          * `effective_text` means a rejected upstream segment contributes its
            original text, so review decisions on one version carry into
            anything built on top of it.

        The instances are constructed, never fetched-and-mutated, and are
        never saved: this method hands out read-only views of transcript rows
        whose `text` is not what the database holds (RULE 3).
        """
        if self.source_revision_id is None:
            return list(self.job.segments.all().order_by('start_time'))
        return self.segments_of_revision(self.source_revision, self.job_id)

    @staticmethod
    def segments_of_revision(revision, job_id):
        """
        One version's text presented as transcript rows -- the shape every
        caller in this feature already understands.

        Public because the dispatch endpoint needs exactly this list before a
        revision row exists, to validate the requested range against it and
        hash the text it is about to pay for.
        """
        return [
            TranscriptSegment(
                id=row.original_segment_id,
                job_id=job_id,
                speaker_label=row.speaker_label,
                start_time=row.start_time,
                end_time=row.end_time,
                text=row.effective_text,
            )
            for row in revision.segments.all().order_by('start_time')
        ]




class AIRevisionSegment(models.Model):
    """
    One segment's AI result, mirroring its original TranscriptSegment 1:1 --
    speaker and start/end time are COPIED at creation and never recalculated,
    so post-processing can only ever change text (RULE 4).

    Both texts are stored, which is what makes the diff review possible
    offline and makes rejection free: `original_text` is the verbatim source
    text (never the sanitized prompt copy), `processed_text` is what the
    model returned.

    `changed` is computed server-side by comparing the two strings, not taken
    from the model's own claim about whether it edited anything -- a
    mislabelled flag would otherwise show the user a diff that isn't there,
    or hide one that is.

    `accepted` defaults to True so a completed revision is immediately
    coherent and exportable without the user having to approve every line.
    Rejecting a segment flips this and effective_text falls back to the
    original, which is why Reject never has to touch -- or restore -- the
    TranscriptSegment underneath.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revision = models.ForeignKey(AIRevision, on_delete=models.CASCADE, related_name='segments')
    original_segment = models.ForeignKey(
        TranscriptSegment, on_delete=models.CASCADE, related_name='ai_revision_segments',
    )
    speaker_label = models.CharField(max_length=100, default='', blank=True)
    start_time = models.FloatField()
    end_time = models.FloatField()
    original_text = models.TextField()
    processed_text = models.TextField()
    changed = models.BooleanField(default=False)
    accepted = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_time']
        indexes = [
            models.Index(fields=['revision', 'start_time'], name='ai_revseg_rev_start_idx'),
        ]

    def __str__(self):
        return f"[{self.start_time:.2f}-{self.end_time:.2f}] {self.speaker_label}: {self.processed_text[:30]}"

    @property
    def effective_text(self) -> str:
        """What this segment reads as in the version view and in exports."""
        if self.changed and self.accepted:
            return self.processed_text
        return self.original_text


class AIUsageRecord(models.Model):
    """
    Append-only ledger of provider spend. Written once per completed
    revision; read by the budget check and the rate limiter.

    Both foreign keys are nullable with on_delete=SET_NULL, which is the
    whole point of a separate ledger table: if usage rows cascaded away with
    their revision or job, deleting them would erase the record of spend and
    reset the daily budget -- turning "delete the revision" into a quota
    bypass. The row outlives whatever it was spent on.

    There is no Redis in this deployment (settings.py uses
    InMemoryChannelLayer and SQLite), so this table IS the counter: the
    budget check sums today's total_tokens and the limiter counts rows in a
    trailing 60-second window. Hence the index on created_at, which both
    queries filter on.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revision = models.ForeignKey(
        AIRevision, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='usage_records',
    )
    job = models.ForeignKey(
        TranscriptionJob, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='ai_usage_records',
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='ai_usage_records',
    )
    operation = models.CharField(max_length=20, blank=True)
    model_name = models.CharField(max_length=100, blank=True)
    # How many provider calls this row represents.
    #
    # Defaults to 1 because that is what every historical row means: the
    # primary pass records one row per batch, i.e. per request. The quality
    # gate records ONE row for a whole run that may have made five calls, so
    # it sets this explicitly. Without it, a requests-per-day budget built on
    # row counts would under-count gated runs by exactly the amount that
    # matters -- and the provider's own quota is measured in requests, not
    # tokens, so that is the number that has to be right.
    request_count = models.PositiveIntegerField(default=1)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.operation} {self.total_tokens} tokens"


class AISegmentCache(models.Model):
    """
    Persistent, segment-level cache for AI transformations (correction, wording,
    translation, etc.).

    Keyed by (operation, source_language, target_language, model_name, prompt_version, content_hash).
    Ensures that unchanged segments are never repeatedly re-sent to Gemini, saving
    thousands of tokens per revision and preventing 429 rate limit pressure.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.CharField(max_length=30, db_index=True)
    source_language = models.CharField(max_length=20, blank=True, default='')
    target_language = models.CharField(max_length=20, blank=True, default='')
    model_name = models.CharField(max_length=100)
    prompt_version = models.CharField(max_length=30, default='v1')
    # NOT db_index=True. Measured live against pg_stat_user_indexes on
    # ~70K real rows: the standalone content_hash index sat at 0 scans --
    # every real lookup filters content_hash together with operation,
    # target_language, model_name and prompt_version (see iter_batches in
    # processor.py), which goes through ai_seg_cache_lookup_idx instead.
    # operation keeps its own db_index=True: THAT standalone index measured
    # 363 real scans, not zero -- it stays until whatever is generating
    # those scans is identified, not removed on the same reasoning that
    # correctly removed this one.
    content_hash = models.CharField(max_length=64)

    source_text_snapshot = models.TextField(blank=True, default='')
    result_text = models.TextField(blank=True, default='')
    changed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=['operation', 'target_language', 'model_name', 'prompt_version', 'content_hash'],
                name='ai_seg_cache_lookup_idx'
            ),
            # RESTORED, DELIBERATELY, AFTER A REAL MISTAKE. Removing
            # db_index=True from content_hash (migration 0021) turned out
            # to drop BOTH indexes Django had tied to that flag -- the
            # plain btree (genuinely 0 scans, correctly dropped) AND this
            # pattern-ops one, which measured 6,348 real scans and was
            # explicitly meant to be left alone. Django does not offer a
            # way to keep only one half of that pair via db_index=True;
            # this explicit Meta.indexes entry is how to have just the
            # half that is actually used. Named freshly rather than
            # reusing the old auto-generated name, since it is now a
            # deliberate index, not a db_index=True side effect.
            models.Index(
                fields=['content_hash'],
                name='ai_seg_cache_hash_like_idx',
                opclasses=['varchar_pattern_ops'],
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['operation', 'source_language', 'target_language', 'model_name', 'prompt_version', 'content_hash'],
                name='unique_ai_seg_cache_entry'
            )
        ]

    def __str__(self):
        return f"AISegmentCache({self.operation}, {self.target_language or self.source_language}, hash={self.content_hash[:8]})"



class AIQualityReport(models.Model):
    """
    The audit trail for one quality run (spec 41): what was reviewed, by
    which model under which prompt version, what it found, how many passes
    it took, and what the gate decided.

    ONE TABLE, NOT TWO. An earlier shape gave every issue its own row, which
    reads well until you notice what the queries actually are: reports are
    fetched whole, for one revision, to render one panel or answer one
    "why was this refused?". No query filters on an individual issue's
    fields. A JSON list therefore costs nothing real and saves a join, a
    migration, and a second model to keep in step -- while the four counts
    that ARE filtered on live denormalized on AIRevision where an index can
    reach them.

    CASCADE, unlike AIUsageRecord's SET_NULL: a usage row is a financial
    record that must outlive what it was spent on, but a quality report
    about a deleted revision describes nothing and is worth nothing.

    Nothing in `report` is provider-facing. Prompts, request bodies and
    credentials never enter it -- it is serialized to the browser (spec 27,
    spec 36).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revision = models.ForeignKey(
        AIRevision, on_delete=models.CASCADE, related_name='quality_reports',
    )
    profile = models.CharField(max_length=30, blank=True, default='')
    status = models.CharField(max_length=30, blank=True, default='')
    reason = models.TextField(blank=True, default='')
    iterations = models.PositiveIntegerField(default=0)
    confidence = models.FloatField(default=0.0)
    model_name = models.CharField(max_length=100, blank=True, default='')
    prompt_version = models.CharField(max_length=20, blank=True, default='')

    # Per-stage telemetry, kept as columns rather than inside `report`
    # because these are the numbers an operator aggregates across runs when
    # deciding whether the pipeline is worth its latency and its bill.
    total_tokens = models.PositiveIntegerField(default=0)
    total_latency_seconds = models.FloatField(default=0.0)

    # QualityResult.to_report(): counts, issues, domain profile, per-stage
    # latencies and tokens. JSONField rather than TextField so a future
    # question about the distribution of issue categories is a query and not
    # a parsing exercise.
    report = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['revision', 'created_at'], name='ai_qrep_rev_created_idx'),
        ]

    def __str__(self):
        return f"{self.revision_id} -> {self.profile} ({self.status})"

    @property
    def issues(self):
        """The stored findings, or [] for a report written before any ran.
        Guarded because `report` is schemaless by construction and a row
        written by an older version of this code must not raise here."""
        raw = self.report or {}
        issues = raw.get('issues')
        return issues if isinstance(issues, list) else []
