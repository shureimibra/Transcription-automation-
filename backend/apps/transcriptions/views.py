from rest_framework.authentication import SessionAuthentication, TokenAuthentication

from .permissions import (
    IsAuthenticatedUser, JobAccessPermission, is_admin, may_access_job,
    may_access_revision, visible_jobs_for,
)

# Every view below authenticates. Declared explicitly rather than relying on
# a project default, because the project default is `AllowAny` with no
# authentication classes -- which is exactly the hole being closed.
#
# Token first, then Session: the same load-bearing order as the workforce API.
# Session first would authenticate a browser by its cookie and then reject the
# request for a missing CSRF token. See apps/workforce/views.py.
API_AUTH = [TokenAuthentication, SessionAuthentication]

from rest_framework import viewsets, status, views
from rest_framework.response import Response
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
import io
import logging

from django.http import Http404
import os
import re
import tempfile
import threading
import uuid
import zipfile
from types import SimpleNamespace

from .models import (
    Project, TranscriptionJob, TranscriptSegment, Translation, TranslatedSegment, DiscoveryTask,
    AcademicFormat, AIRevision, AIRevisionSegment,
)
from .serializers import (
    ProjectSerializer, TranscriptionJobSerializer, TranscriptSegmentSerializer,
    TranslationSerializer, AcademicFormatSerializer,
    AIRevisionSerializer, AIRevisionSummarySerializer, AIRevisionSegmentSerializer,
    AIQualityReportSerializer,
)
from .job_cleanup import JobCleanupService
from . import ai_budget

# Helper import to trigger celery task in production
from .tasks import (
    run_transcription_pipeline, run_series_pipeline, run_translation_pipeline,
    run_discovery_task, run_academic_format_pipeline, run_ai_revision_pipeline,
)

try:
    from backend.core.services.languages import PLATFORM_LANGUAGES, TRANSLATION_LANGUAGES, is_translatable
    from backend.core.services import export_formats
    from backend.core.services.profiling import profile_stage
    from backend.core.services.auth_helper import parse_auth_input
    from backend.core.services.academic import compute_source_text_hash
    from backend.core.services.ai_assist import (
        AIAssistError, AIBudgetExceededError, AIRateLimitedError,
        MAX_CUSTOM_INSTRUCTION_CHARS, OPERATION_LABELS, OPERATIONS,
        compute_request_hash, is_configured as ai_is_configured, resolve_model_name,
        config as ai_config,
        default_profile_for as ai_default_profile_for,
        get_profile as ai_get_profile,
        profile_names as ai_profile_names,
    )
except ModuleNotFoundError:
    from core.services.languages import PLATFORM_LANGUAGES, TRANSLATION_LANGUAGES, is_translatable
    from core.services import export_formats
    from core.services.profiling import profile_stage
    from core.services.auth_helper import parse_auth_input
    from core.services.academic import compute_source_text_hash
    from core.services.ai_assist import (
        AIAssistError, AIBudgetExceededError, AIRateLimitedError,
        MAX_CUSTOM_INSTRUCTION_CHARS, OPERATION_LABELS, OPERATIONS,
        compute_request_hash, is_configured as ai_is_configured, resolve_model_name,
        config as ai_config,
        default_profile_for as ai_default_profile_for,
        get_profile as ai_get_profile,
        profile_names as ai_profile_names,
    )

# Shared with tasks.py's AI pipeline logger so an operator can follow one
# revision from its dispatch decision through to its provider calls.
logger = logging.getLogger("AIPipeline")


def _in_own_connection(target):
    """
    Wrap a callable that will run in its OWN background thread so its
    Django DB connection is guaranteed closed when the thread finishes --
    the same fix apps.workforce.warmpool already applies via
    `_warm_guarded`, extended here to the transcription pipeline's thread.

    Django opens a connection per thread and never closes it on its own.
    A view that spins up a thread per upload leaks one connection per
    upload, and a long test run (or a long production day) accumulates
    enough of them to exhaust what Postgres allows -- which is what a full
    `apps.transcriptions` run was doing: 129 cascading "the connection is
    closed" errors, all downstream of transcription-pipeline threads that
    never closed the connection they opened.

    APPLIED AT THE THREAD BOUNDARY, DELIBERATELY, NOT INSIDE THE PIPELINE
    FUNCTION ITSELF. `run_transcription_pipeline` is also called directly
    and synchronously on the MAIN thread by a large number of tests --
    closing there would attack the wrong connection: Django's own `close()`
    does not null the reference while `in_atomic_block` is True (every
    TestCase test body), so it would leave that connection dangling rather
    than reconnecting, reproducing the exact symptom this fixes, self
    -inflicted, on every synchronous caller. The wrapper is applied only at
    `threading.Thread(target=...)`, so only a genuine background thread's
    own, never-shared connection is ever touched.
    """
    import functools
    from django.db import connection

    @functools.wraps(target)
    def _wrapped(*args, **kwargs):
        try:
            return target(*args, **kwargs)
        finally:
            connection.close()
    return _wrapped


class SupportedLanguagesView(views.APIView):
    """
    Canonical language catalog for the frontend's language dropdown -- keeps
    frontend and backend synchronized on exactly what the *platform* (across
    all transcription engines, not just Whisper) can use, since this is
    generated from the same core.services.languages module the transcription
    pipeline itself validates against.
    """
    authentication_classes = API_AUTH
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        languages = [
            {'code': code, 'name': info['name'], 'engine': info['engine']}
            for code, info in PLATFORM_LANGUAGES.items()
        ]
        languages.sort(key=lambda item: item['name'])
        return Response(languages)


class SupportedTranslationLanguagesView(views.APIView):
    """Canonical target-language catalog for the 'Translate To' dropdown."""
    authentication_classes = API_AUTH
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        languages = [
            {'code': code, 'name': info['name'], 'engine': info['engine']}
            for code, info in TRANSLATION_LANGUAGES.items()
        ]
        languages.sort(key=lambda item: item['name'])
        return Response(languages)


class DiscoverSeriesView(views.APIView):
    """
    Optional multi-page media discovery (Phase 5/6). Given a URL, reports
    whether it's part of a detectable playlist/episode series/course, and if
    so, the ordered list of related items -- without creating any jobs or
    downloading anything. The frontend uses this to offer the user a choice
    (current page only / + related / entire series) before committing.

    Runs in a background thread rather than blocking this request: some
    discovery strategies (yt-dlp playlist checks, page scraping) can take
    anywhere from milliseconds to over a minute depending on the site, and
    holding the request/response cycle open for that long ties up a server
    worker and can stall other concurrent requests. The frontend polls
    DiscoverySeriesStatusView by task id instead -- same eventual result,
    same UX, just non-blocking.

    Never assumes every site supports this: a site with no detectable
    structure returns is_series=False, and the normal single-URL flow is
    unaffected.
    """
    authentication_classes = API_AUTH
    permission_classes = [IsAuthenticatedUser]

    def post(self, request):
        from core.services.url_guard import UnsafeURL, check_url

        url = request.data.get('source_url')
        if not url:
            return Response({'error': 'source_url is required'}, status=status.HTTP_400_BAD_REQUEST)

        # REFUSE ANYTHING POINTING BACK INSIDE THE NETWORK.
        #
        # This endpoint takes an address and fetches it server-side, which
        # without a check is also "make a request from inside my network to
        # anywhere you like and show me the result". On a cloud box the
        # address that matters is 169.254.169.254, where most providers
        # serve instance credentials to anything that asks.
        #
        # The check existed in dynamic_discovery and was called from
        # nowhere but its own tests. This is the choke point it needed.
        try:
            url = check_url(url)
        except UnsafeURL as exc:
            return Response({'error': str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)

        # Optional per-request auth for login-walled pages (see
        # core.services.auth_helper) -- never logged, since it may contain
        # live session credentials.
        auth_headers = parse_auth_input(
            pasted_text=request.data.get('site_auth_text'),
            cookies_txt_content=request.data.get('site_auth_cookies_file'),
        ) or None

        # Explicit opt-in for SequentialQueryParamStrategy -- see
        # core.services.discovery.sequential_param_strategy for why this
        # isn't tried automatically.
        try_sequential = bool(request.data.get('try_sequential', False))

        task = DiscoveryTask.objects.create(
            source_url=url, site_auth_headers=auth_headers, try_sequential=try_sequential,
        )
        thread = threading.Thread(target=run_discovery_task, args=(task.id,))
        thread.daemon = True
        thread.start()

        return Response({'task_id': str(task.id), 'status': 'pending'}, status=status.HTTP_202_ACCEPTED)


class DiscoverySeriesStatusView(views.APIView):
    """Polled by the frontend until a DiscoverSeriesView task completes."""
    authentication_classes = API_AUTH
    permission_classes = [IsAuthenticatedUser]

    def get(self, request, task_id):
        task = get_object_or_404(DiscoveryTask, id=task_id)
        response = {'status': task.status}
        if task.status == 'completed':
            response['result'] = task.result
        elif task.status == 'failed':
            response['error'] = task.error_message
        return Response(response)


class ProjectViewSet(viewsets.ModelViewSet):
    authentication_classes = API_AUTH
    permission_classes = [IsAuthenticatedUser]

    queryset = Project.objects.all().order_by('-created_at')
    serializer_class = ProjectSerializer

class TranscriptionJobViewSet(viewsets.ModelViewSet):
    """
    Transcription jobs, scoped to the caller.

    THE QUERYSET IS THE BOUNDARY. `get_queryset` filters through
    `visible_jobs_for`, so list, retrieve, update and delete are all scoped
    by construction rather than by each method remembering to check. Before
    this, `queryset = TranscriptionJob.objects.all()` served every job to
    every anonymous caller.
    """

    authentication_classes = API_AUTH
    permission_classes = [IsAuthenticatedUser, JobAccessPermission]
    serializer_class = TranscriptionJobSerializer

    # Kept for DRF's router basename inference only. Every read goes through
    # get_queryset below; nothing should use this attribute directly.
    queryset = TranscriptionJob.objects.all().order_by('-created_at')

    def get_queryset(self):
        return visible_jobs_for(self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        # Stamp ownership at creation. Everything downstream -- visibility,
        # export, AI revisions, the WebSocket -- keys off this.
        serializer.save(owner=self.request.user)

    def destroy(self, request, *args, **kwargs):
        """
        DELETE /transcriptions/<id>/ -- permanently removes the job and
        everything associated with it (see JobCleanupService: DB records
        via cascade, leftover temp audio files, orphaned discovery
        records). Segments/translations already CASCADE at the DB level;
        this adds the filesystem + DiscoveryTask cleanup the ORM can't do
        on its own.
        """
        job = self.get_object()
        summary = JobCleanupService.delete_job(job)
        return Response(summary, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        # A plain dict built explicitly WITHOUT the uploaded file, not
        # request.data.copy(): QueryDict.copy() deep-copies every value it
        # holds, and for multipart requests request.data includes the
        # uploaded file object alongside the regular fields. That deep-copy
        # works by accident for small files (Django keeps them in memory,
        # which happens to be copyable) but crashes with "cannot pickle
        # '_io.BufferedRandom' object" for anything over
        # FILE_UPLOAD_MAX_MEMORY_SIZE (2.5MB default -- i.e. almost any real
        # audio/video file), since Django spools those to an actual disk
        # file handle instead. The file is handled entirely separately below
        # via request.FILES, so it was never actually needed in this dict.
        data = {k: v for k, v in request.data.items() if k != 'file'}
        file_obj = request.FILES.get('file')
        temp_file_path = None

        if file_obj is not None:
            suffix = os.path.splitext(file_obj.name)[1] or '.bin'
            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix='upload_', dir=None)
            for chunk in file_obj.chunks():
                tmp_file.write(chunk)
            tmp_file.close()
            temp_file_path = tmp_file.name
            data['input_source'] = 'upload'
            data['source_url'] = ''

        # Optional per-request auth for login-walled source URLs (see
        # core.services.auth_helper) -- never logged, since it may contain
        # live session credentials. Not part of the serializer's fields
        # (never read back through the API), same pattern as file_path below.
        auth_headers = parse_auth_input(
            pasted_text=data.get('site_auth_text'),
            cookies_txt_content=data.get('site_auth_cookies_file'),
        ) or None

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        if temp_file_path:
            job = serializer.save(file_path=temp_file_path, site_auth_headers=auth_headers,
                                  owner=request.user)
        else:
            job = serializer.save(site_auth_headers=auth_headers, owner=request.user)

        # Trigger the pipeline task asynchronously. Prefer Celery when configured;
        # otherwise run the task in a background thread to avoid blocking the request.
        from django.conf import settings
        try:
            # Prefer calling the original function in a background thread to avoid
            # blocking when Celery or its broker is unavailable. If the task object
            # exposes a `.run` attribute (Celery Task), call that; otherwise call
            # the callable directly.
            import threading
            target_fn = getattr(run_transcription_pipeline, 'run', run_transcription_pipeline)
            thread = threading.Thread(target=_in_own_connection(target_fn), args=(job.id,))
            thread.daemon = True
            thread.start()

            logger_message = f"Triggered background transcription pipeline for Job ID: {job.id}"
            print(logger_message)
        except Exception as e:
            print(f"Failed to trigger background task for Job {job.id}: {e}")

        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def segments(self, request, pk=None):
        job = self.get_object()
        segments = job.segments.all()
        serializer = TranscriptSegmentSerializer(segments, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def speaker_aliases(self, request, pk=None):
        """
        Sets/updates this job's speaker display-name aliases (e.g.
        {"Speaker 0": "Interviewer"}). Renaming never touches the
        underlying TranscriptSegment/TranslatedSegment rows -- only this
        job-level lookup dict, which every export and the live preview
        apply at display time (see export_formats.apply_speaker_aliases).
        That's what makes a rename instant everywhere and trivially
        reversible.

        Body: {"aliases": {"Speaker 0": "Interviewer"}} merges into the
        existing map (mapping a label to an empty string reverts just that
        one speaker back to its raw label); {"reset": true} clears every
        alias for this job back to raw labels in one step.
        """
        job = self.get_object()
        if request.data.get('reset'):
            job.speaker_aliases = {}
        else:
            aliases = request.data.get('aliases')
            if not isinstance(aliases, dict):
                return Response(
                    {'error': 'aliases must be an object mapping speaker labels to display names.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            updated = dict(job.speaker_aliases or {})
            for raw_label, alias in aliases.items():
                if alias:
                    updated[raw_label] = alias
                else:
                    updated.pop(raw_label, None)
            job.speaker_aliases = updated
        job.save(update_fields=['speaker_aliases'])
        return Response(self.get_serializer(job).data, status=status.HTTP_200_OK)

    CANCELLABLE_STATUSES = {'queued', 'downloading', 'transcribing', 'diarizing'}

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """
        Requests cancellation of an in-progress job. Sets cancel_requested
        and returns immediately -- the actual stop happens on the
        background thread already running the pipeline, which polls this
        flag (see tasks._cancel_check_for): within ~0.5s while yt-dlp is
        downloading, or before the next stage/segment otherwise. Does not
        touch already-produced transcript data (see JobCleanupService for
        actual deletion, which this never triggers).

        A no-op (200, unchanged) if the job has already left a cancellable
        state -- completed/failed/cancelled jobs can't be cancelled again,
        and this must never overwrite a finished result.
        """
        job = self.get_object()
        if job.status not in self.CANCELLABLE_STATUSES:
            return Response(
                {'error': f"Job is not in a cancellable state (status: {job.status})."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        job.cancel_requested = True
        job.save(update_fields=['cancel_requested'])
        return Response(self.get_serializer(job).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def cancel_translation(self, request, pk=None):
        """Same cancellation model as cancel() above, but for one of this
        job's translations (identified by target language)."""
        job = self.get_object()
        language = request.data.get('language')
        if not language:
            return Response({'error': 'language is required'}, status=status.HTTP_400_BAD_REQUEST)

        translation = get_object_or_404(Translation, job=job, language=language)
        if translation.status not in ('queued', 'translating'):
            return Response(
                {'error': f"Translation is not in a cancellable state (status: {translation.status})."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        translation.cancel_requested = True
        translation.save(update_fields=['cancel_requested'])
        return Response(TranslationSerializer(translation).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def translate(self, request, pk=None):
        """
        Translation is a separate, on-demand stage: it never runs as part of
        transcribing, only when explicitly requested here. Caches by
        (job, language) -- requesting a language that's already translated
        returns the existing Translation immediately instead of re-running
        the model, per the caching requirement.

        A *failed* translation is explicitly not cached the same way: caching
        by (job, language) via get_or_create means a prior failure (e.g. the
        engine-selection bug that crashed on an NLLB-only source language)
        would otherwise be returned forever on every retry without ever
        actually running again, since get_or_create only creates a row once.
        A failed row is reset and re-triggered instead of just returned.
        """
        job = self.get_object()
        language = request.data.get('language')
        if not language:
            return Response({'error': 'language is required'}, status=status.HTTP_400_BAD_REQUEST)
        if not is_translatable(language):
            return Response({'error': f"Translation into '{language}' is not supported."}, status=status.HTTP_400_BAD_REQUEST)

        translation, created = Translation.objects.get_or_create(job=job, language=language)
        if not created and translation.status != 'failed':
            return Response(TranslationSerializer(translation).data, status=status.HTTP_200_OK)

        if not created:
            translation.status = 'queued'
            translation.error_message = ''
            translation.completed_at = None
            translation.save(update_fields=['status', 'error_message', 'completed_at'])
            translation.segments.all().delete()

        thread = threading.Thread(target=run_translation_pipeline, args=(translation.id,))
        thread.daemon = True
        thread.start()
        print(f"Triggered translation pipeline for Translation {translation.id} (job {job.id} -> {language})")

        return Response(TranslationSerializer(translation).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def translations(self, request, pk=None):
        """Lists every cached translation for this job (id, language, status)."""
        job = self.get_object()
        serializer = TranslationSerializer(job.translations.all(), many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def academic_format(self, request, pk=None):
        """
        Triggers (or returns cached) academic notation formatting for this
        job -- see core.services.academic and tasks.run_academic_format_pipeline.
        Same on-demand, background-thread, non-destructive-to-the-original
        shape as translate() above, with two differences:

        - No `language`-like parameter: subject is *detected*, not chosen,
          so AcademicFormat is a OneToOneField and there's exactly one per
          job (get_or_create(job=job) is the whole caching key).
        - Cache validity also depends on source_text_hash: segment text can
          be edited after the fact (see EditSegmentView), so "a completed
          row already exists" isn't sufficient on its own -- it must also
          have been generated from the segments as they currently are.
          A hash mismatch is treated exactly like Translation treats a
          'failed' row: reset and re-run, never cached forever.
        """
        job = self.get_object()
        current_hash = compute_source_text_hash(job)

        academic_format, created = AcademicFormat.objects.get_or_create(job=job)
        if not created and academic_format.status == 'completed' and academic_format.source_text_hash == current_hash:
            return Response(AcademicFormatSerializer(academic_format).data, status=status.HTTP_200_OK)

        if not created and academic_format.status in ('queued', 'detecting', 'formatting', 'validating'):
            # Already running against the current segments -- no point
            # starting a second pipeline for the same request.
            return Response(AcademicFormatSerializer(academic_format).data, status=status.HTTP_200_OK)

        if not created:
            academic_format.status = 'queued'
            academic_format.error_message = ''
            academic_format.completed_at = None
            academic_format.cancel_requested = False
            academic_format.subject = ''
            academic_format.subject_confidence = None
            academic_format.save()
            academic_format.segments.all().delete()

        academic_format.source_text_hash = current_hash
        academic_format.save(update_fields=['source_text_hash'])

        thread = threading.Thread(target=run_academic_format_pipeline, args=(academic_format.id,))
        thread.daemon = True
        thread.start()
        print(f"Triggered academic format pipeline for AcademicFormat {academic_format.id} (job {job.id})")

        return Response(AcademicFormatSerializer(academic_format).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def cancel_academic_format(self, request, pk=None):
        """Same cancellation model as cancel_translation() above, for this
        job's (at most one) AcademicFormat."""
        job = self.get_object()
        academic_format = get_object_or_404(AcademicFormat, job=job)
        if academic_format.status not in ('queued', 'detecting', 'formatting', 'validating'):
            return Response(
                {'error': f"Academic format is not in a cancellable state (status: {academic_format.status})."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        academic_format.cancel_requested = True
        academic_format.save(update_fields=['cancel_requested'])
        return Response(AcademicFormatSerializer(academic_format).data, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------ #
    # AI Assist (Gemini). See core.services.ai_assist for the processing
    # layer and ai_budget for the cost controls every dispatch goes through.
    # ------------------------------------------------------------------ #

    AI_ACTIVE_STATUSES = ('queued', 'processing')

    # Keys the metadata action will store. A whitelist, not a merge of
    # whatever arrives: document_metadata is a JSONField, so without this a
    # client could park an arbitrarily large or arbitrarily shaped blob on the
    # job, and anything stored here can end up in an exported document header.
    METADATA_FIELDS = (
        'title', 'owner', 'recorded_date', 'location',
        'subject', 'tags', 'notes', 'speakers',
    )
    MAX_METADATA_VALUE_CHARS = 500

    def _resolve_ai_range(self, request, segments):
        """
        Validates segment_start_index/segment_end_index against the job's
        segments. Returns (start, end, sliced_segments) or raises ValueError
        with a client-safe message.

        Semantics are a Python slice: start inclusive, end EXCLUSIVE, an
        omitted or null end meaning "through the last segment" -- which is
        exactly the "start from here to the end of the transcription" case.
        Indexes are validated against the current ordering here and stored on
        the revision, so a later request can't widen a range past the
        transcript.
        """
        total = len(segments)

        def _as_index(value, field):
            if value in (None, ''):
                return None
            try:
                index = int(value)
            except (TypeError, ValueError):
                raise ValueError(f'{field} must be an integer.')
            if isinstance(value, bool):
                raise ValueError(f'{field} must be an integer.')
            return index

        start = _as_index(request.data.get('segment_start_index'), 'segment_start_index')
        end = _as_index(request.data.get('segment_end_index'), 'segment_end_index')

        start = 0 if start is None else start
        if start < 0 or start >= total:
            raise ValueError(
                f'segment_start_index must be between 0 and {total - 1} '
                f'({total} segments in this transcript).'
            )
        if end is not None:
            if end <= start:
                raise ValueError('segment_end_index must be greater than segment_start_index.')
            if end > total:
                raise ValueError(f'segment_end_index must be at most {total}.')

        return start, end, segments[start:end if end is not None else total]

    @action(detail=True, methods=['get', 'post'], url_path='ai_revisions')
    def ai_revisions(self, request, pk=None):
        """
        GET  lists this job's AI revisions (summaries, no segments).
        POST validates, checks cost, and dispatches a new one.

        Shaped like academic_format() above -- validate, return a cached
        result if we already have one, otherwise reset/create and hand off to
        a background thread -- with three additions that only matter once a
        paid external API is involved:

          * The cache key is a request_hash over (range text + operation +
            languages + model + custom instruction), not just (job, operation).
            Two different custom prompts are two different results, and an
            edited transcript invalidates both.
          * Cost is checked BEFORE dispatch (ai_budget.preflight), so a
            refusal costs nothing and comes back as a clean 429 rather than a
            provider error surfacing later on a background thread.
          * Only one revision per job runs at a time. Each concurrent run
            spends real tokens, and there is no queue to put a second one in
            (SQLite + threads, no Celery broker).

        RULE 3: nothing here writes to TranscriptSegment. A revision is new
        rows in a separate table; the original stays the source of truth.
        """
        job = self.get_object()

        if request.method == 'GET':
            return Response(
                AIRevisionSummarySerializer(job.ai_revisions.all(), many=True).data
            )

        # The server decides entitlement, never the client -- so this is
        # checked here even though /ai-capabilities/ already told the frontend.
        if not ai_is_configured():
            return Response(
                {'error': 'AI assistance is not configured on this server.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        operation = str(request.data.get('operation') or '').strip()
        if operation not in OPERATIONS:
            return Response(
                {'error': f"operation must be one of: {', '.join(OPERATIONS)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Normalized to None/'' for every operation that doesn't use them, so
        # a stray target_language can't fork the cache key for a Correct run.
        target_language = str(request.data.get('target_language') or '').strip() or None
        if operation == 'translate':
            if not target_language:
                return Response(
                    {'error': 'target_language is required for the translate operation.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not is_translatable(target_language):
                return Response(
                    {'error': f"Translation into '{target_language}' is not supported."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            target_language = None

        # Which quality profile, if any, gates this run.
        #
        # OPT-IN, and the configured default is 'none'. Every request that
        # predates this feature -- and every client that does not know about
        # it -- therefore behaves exactly as it did: one AI pass, no gate, no
        # extra spend (RULE 17, RULE 18). Setting AI_QUALITY_DEFAULT_PROFILE
        # to 'auto' turns it on server-wide for translations, which is the
        # recommended production setting; the UI sends it explicitly either
        # way.
        #
        # Validated against the server's own registry rather than trusted,
        # for the same reason `operation` is: an unknown profile name would
        # otherwise be stored, skip the gate at run time, and leave a
        # revision claiming a QA profile that never ran.
        quality_profile = str(request.data.get('quality_profile') or '').strip().lower()
        if not quality_profile:
            quality_profile = ai_default_profile_for(
                operation, ai_config('AI_QUALITY_DEFAULT_PROFILE', 'none'),
            )
        if quality_profile and quality_profile != 'none' and ai_get_profile(quality_profile) is None:
            return Response(
                {'error': (
                    'quality_profile must be one of: '
                    + ', '.join(ai_profile_names()) + '.'
                )},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if quality_profile == 'none':
            quality_profile = ''

        custom_instruction = str(request.data.get('custom_instruction') or '').strip()
        if operation == 'custom':
            if not custom_instruction:
                return Response(
                    {'error': 'custom_instruction is required for the custom operation.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if len(custom_instruction) > MAX_CUSTOM_INSTRUCTION_CHARS:
                return Response(
                    {'error': (
                        'custom_instruction must be at most '
                        f'{MAX_CUSTOM_INSTRUCTION_CHARS} characters '
                        f'(got {len(custom_instruction)}).'
                    )},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            custom_instruction = ''

        all_segments = list(job.segments.all().order_by('start_time'))
        if not all_segments:
            return Response(
                {'error': 'This job has no transcript segments to process.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Chaining. Without this, every operation forks off the raw ASR text
        # and "translate the version I just corrected" is impossible -- which
        # is the request that matters most, since translating uncorrected ASR
        # output propagates its mistakes into a second language.
        #
        # The source is a *completed* revision of THIS job. Anything else is
        # refused rather than silently falling back to the original, because a
        # caller asking to build on a specific version has to be told if that
        # is not what happened.
        source_revision = None
        source_revision_id = request.data.get('source_revision')
        if source_revision_id:
            source_revision = get_object_or_404(
                AIRevision, pk=source_revision_id, job=job
            )
            if source_revision.status != 'completed':
                return Response(
                    {'error': (
                        'The selected source version is not finished '
                        f'(status: {source_revision.status}). Only a completed '
                        'version can be processed further.'
                    )},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            base_segments = AIRevision.segments_of_revision(source_revision, job.id)
            if not base_segments:
                return Response(
                    {'error': 'The selected source version has no segments.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            base_segments = all_segments

        try:
            start_index, end_index, segments = self._resolve_ai_range(request, base_segments)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        model_name = resolve_model_name()
        source_language = job.language or job.detected_language or None
        request_hash = compute_request_hash(
            segments,
            operation,
            model_name,
            target_language=target_language,
            source_language=source_language,
            custom_instruction=custom_instruction,
            source_revision_id=source_revision.id if source_revision else None,
            quality_profile=quality_profile,
        )

        # Cache hit: identical request already paid for. Returned instead of
        # re-billing, which is the whole point of hashing the request.
        cached = job.ai_revisions.filter(
            request_hash=request_hash, status='completed'
        ).first()
        if cached:
            return Response(AIRevisionSerializer(cached).data, status=status.HTTP_200_OK)

        in_flight = job.ai_revisions.filter(
            request_hash=request_hash, status__in=self.AI_ACTIVE_STATUSES
        ).first()
        if in_flight:
            return Response(AIRevisionSerializer(in_flight).data, status=status.HTTP_200_OK)

        if job.ai_revisions.filter(status__in=self.AI_ACTIVE_STATUSES).exists():
            return Response(
                {'error': (
                    'Another AI revision is already running for this job. '
                    'Wait for it to finish, or cancel it first.'
                )},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            ai_budget.preflight(segments, quality_profile=quality_profile)
        except (AIRateLimitedError, AIBudgetExceededError) as exc:
            # .safe_message, never .detail: the detail names internal limits
            # and current spend, which is operator information.
            logger.warning('[AI ASSIST] Refused dispatch for job %s: %s', job.id, exc.detail)
            return Response(
                {'error': exc.safe_message},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except AIAssistError as exc:
            logger.error('[AI ASSIST] Pre-flight failed for job %s: %s', job.id, exc.detail)
            return Response(
                {'error': exc.safe_message},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        revision = AIRevision.objects.create(
            job=job,
            operation=operation,
            label=self._ai_revision_label(operation, target_language, source_revision),
            custom_instruction=custom_instruction,
            source_language=source_language,
            target_language=target_language,
            model_name=model_name,
            quality_profile=quality_profile,
            # 'pending' the moment a gated revision exists, so the window
            # between dispatch and the gate starting is not reported as
            # 'not_run' -- which would claim no gate was requested at all.
            quality_status='pending' if quality_profile else 'not_run',
            segment_start_index=start_index,
            segment_end_index=end_index,
            source_revision=source_revision,
            request_hash=request_hash,
            # Whole-job hash, not the range's: it answers "is this revision
            # still based on the current transcript?", and an edit anywhere
            # can change how the range should read. Still the JOB's hash for a
            # chained revision -- an edit to the original makes every version
            # downstream of it stale too.
            source_text_hash=compute_source_text_hash(job),
        )

        thread = threading.Thread(target=run_ai_revision_pipeline, args=(revision.id,))
        thread.daemon = True
        thread.start()
        print(
            f"Triggered AI revision {revision.id} ({operation}) for job {job.id} "
            f"[segments {start_index}:{end_index if end_index is not None else len(base_segments)}"
            f"{' of ' + str(source_revision.id) if source_revision else ''}]"
        )

        return Response(AIRevisionSerializer(revision).data, status=status.HTTP_201_CREATED)

    @staticmethod
    def _ai_revision_label(operation, target_language, source_revision=None):
        """Server-derived display name. Not client-supplied: it labels a
        version in the switcher and is written into exported documents."""
        label = OPERATION_LABELS.get(operation, operation.title())
        if operation == 'translate' and target_language:
            language = PLATFORM_LANGUAGES.get(target_language, {}).get('name') or target_language
            label = f'{label} ({language})'
        if source_revision is not None:
            # Lineage in the name, because in a version switcher "Translated
            # (Swahili)" twice over -- once from the raw transcript, once from
            # the corrected one -- is otherwise indistinguishable.
            label = f'{label} from {source_revision.label or source_revision.operation}'
        return label[:120]

    @action(detail=True, methods=['post'])
    def cancel_ai_revision(self, request, pk=None):
        """
        Same cancellation model as cancel_academic_format() above: sets the
        flag, returns immediately. run_ai_revision_pipeline polls it between
        batches with a fresh DB read, so the stop lands before the next
        provider call -- which is also where it saves money.

        Body: {"revision": "<uuid>"}, or omitted to cancel whichever revision
        for this job is currently running.
        """
        job = self.get_object()
        revision_id = request.data.get('revision')
        if revision_id:
            revision = get_object_or_404(AIRevision, pk=revision_id, job=job)
        else:
            revision = job.ai_revisions.filter(
                status__in=self.AI_ACTIVE_STATUSES
            ).first()
            if revision is None:
                return Response(
                    {'error': 'No AI revision is currently running for this job.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if revision.status not in self.AI_ACTIVE_STATUSES:
            return Response(
                {'error': f"AI revision is not in a cancellable state (status: {revision.status})."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        revision.cancel_requested = True
        revision.save(update_fields=['cancel_requested'])
        return Response(AIRevisionSerializer(revision).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='metadata')
    def document_metadata(self, request, pk=None):
        """
        Edits the document-level metadata the user wants on the finished
        product -- title, owner, date, subject, tags, notes.

        Follows the speaker_aliases action's shape exactly (merge by default,
        {"reset": true} to clear) for the same reason: it's job-level display
        data that exports read at render time, so an edit is instant
        everywhere and reversible without touching a single segment.

        Only METADATA_FIELDS keys are stored, and each value is length-capped.
        Unknown keys are reported rather than silently dropped, so a client
        typo doesn't look like a save that worked.
        """
        job = self.get_object()

        if request.data.get('reset'):
            job.document_metadata = {}
            job.save(update_fields=['document_metadata'])
            return Response(self.get_serializer(job).data, status=status.HTTP_200_OK)

        metadata = request.data.get('metadata')
        if not isinstance(metadata, dict):
            return Response(
                {'error': 'metadata must be an object of document fields.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        unknown = sorted(set(metadata) - set(self.METADATA_FIELDS))
        if unknown:
            return Response(
                {'error': (
                    f"Unsupported metadata field(s): {', '.join(unknown)}. "
                    f"Allowed: {', '.join(self.METADATA_FIELDS)}."
                )},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated = dict(job.document_metadata or {})
        for key, value in metadata.items():
            if value in (None, ''):
                # Same convention as speaker_aliases: an empty value clears
                # just that one field rather than requiring a full reset.
                updated.pop(key, None)
                continue
            if isinstance(value, (list, tuple)):
                # `tags` and `speakers` are lists; everything else is scalar.
                cleaned = [str(item).strip()[:self.MAX_METADATA_VALUE_CHARS]
                           for item in value if str(item).strip()]
                if not cleaned:
                    updated.pop(key, None)
                    continue
                updated[key] = cleaned
                continue
            if isinstance(value, (dict,)):
                return Response(
                    {'error': f"metadata.{key} must be a string or a list of strings."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            text = str(value).strip()
            if len(text) > self.MAX_METADATA_VALUE_CHARS:
                return Response(
                    {'error': (
                        f'metadata.{key} must be at most '
                        f'{self.MAX_METADATA_VALUE_CHARS} characters.'
                    )},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            updated[key] = text

        job.document_metadata = updated
        job.save(update_fields=['document_metadata'])
        return Response(self.get_serializer(job).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def create_series(self, request):
        """
        Creates one TranscriptionJob per item in an ordered list (typically
        the 'items' returned by DiscoverSeriesView, possibly trimmed to the
        subset the user chose), sharing a series_id, and processes them
        sequentially in order via run_series_pipeline -- preserving
        ordering and avoiding the CPU contention concurrent heavy
        transcription jobs cause.
        """
        project_id = request.data.get('project')
        items = request.data.get('items')
        language = request.data.get('language') or None

        if not project_id or not items:
            return Response({'error': 'project and items are required'}, status=status.HTTP_400_BAD_REQUEST)

        # Optional per-request auth (see core.services.auth_helper), applied
        # to every item in the series -- they're all typically from the
        # same site/session. Never logged.
        auth_headers = parse_auth_input(
            pasted_text=request.data.get('site_auth_text'),
            cookies_txt_content=request.data.get('site_auth_cookies_file'),
        ) or None

        series_id = uuid.uuid4()
        jobs = []
        for idx, item in enumerate(items):
            item_url = item.get('url') if isinstance(item, dict) else item
            item_title = item.get('title', '') if isinstance(item, dict) else ''
            if not item_url:
                continue
            job = TranscriptionJob.objects.create(
                project_id=project_id,
                input_source='url',
                source_url=item_url,
                language=language,
                series_id=series_id,
                series_index=idx,
                series_title=item_title,
                site_auth_headers=auth_headers,
            )
            jobs.append(job)

        if not jobs:
            return Response({'error': 'No valid items provided.'}, status=status.HTTP_400_BAD_REQUEST)

        thread = threading.Thread(target=run_series_pipeline, args=(series_id,))
        thread.daemon = True
        thread.start()
        print(f"Triggered series pipeline for series {series_id} ({len(jobs)} item(s))")

        serializer = self.get_serializer(jobs, many=True)
        return Response(
            {'series_id': str(series_id), 'jobs': serializer.data},
            status=status.HTTP_201_CREATED,
        )


class EditSegmentView(views.APIView):
    """
    Manual transcript editing -- inserting annotation tags ([laughter],
    [crosstalk]) at a cursor position, correcting words, and reassigning a
    turn to a different speaker.

    TIMESTAMPS REMAIN IMMUTABLE. `start_time`, `end_time` and `confidence`
    come from the pipeline and describe where in the audio the segment
    actually is; rewriting them here would desync the segment from the sound
    it was produced from, and Eagle grades timing.

    SPEAKER LABELS ARE NOW EDITABLE, and that is a deliberate reversal. This
    view used to refuse them on the same reasoning as the timestamps. But on
    an Eagle transcription task, speaker identification IS the graded work:
    diarization gets turn boundaries wrong -- normally and unavoidably -- and
    correcting exactly that is what the reviewer is paid for. A global rename
    (`speaker_aliases`) cannot fix it, because the error is per-segment: part
    of one person's speech attributed to another. Refusing the edit would
    leave the reviewer unable to do the one thing the task asks of them.

    Editing the original transcript does NOT retroactively touch any
    already-completed translation of it -- translations are their own
    independent artifact (see the Translation model's own docstring);
    picking up an edit requires explicitly re-requesting that translation,
    exactly like requesting one for the first time.
    """
    authentication_classes = API_AUTH
    permission_classes = [IsAuthenticatedUser]

    #: What a caller may change. Anything absent from this set is refused,
    #: rather than silently ignored -- a client that thinks it moved a
    #: timestamp should be told it did not.
    EDITABLE_FIELDS = ('text', 'speaker_label')

    def patch(self, request, job_id, segment_id):
        segment = get_object_or_404(TranscriptSegment, id=segment_id, job_id=job_id)
        if not may_access_job(request.user, segment.job):
            raise Http404

        submitted = {k: v for k, v in request.data.items() if k in self.EDITABLE_FIELDS}
        rejected = [k for k in request.data if k not in self.EDITABLE_FIELDS]
        if rejected:
            return Response(
                {'error': f'These fields cannot be edited: {", ".join(sorted(rejected))}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not submitted:
            return Response(
                {'error': 'Provide text and/or speaker_label.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        changed = []
        if 'text' in submitted:
            if submitted['text'] is None:
                return Response({'error': 'text cannot be null.'},
                                status=status.HTTP_400_BAD_REQUEST)
            segment.text = submitted['text']
            changed.append('text')
        if 'speaker_label' in submitted:
            label = str(submitted['speaker_label'] or '').strip()[:100]
            segment.speaker_label = label
            changed.append('speaker_label')

        segment.save(update_fields=changed)
        return Response(TranscriptSegmentSerializer(segment).data, status=status.HTTP_200_OK)


class AICapabilitiesView(views.APIView):
    """
    The server telling the client what AI Assist is permitted to do:
    whether a key is configured, which operations exist, and how much of the
    token budget is left.

    This exists so the frontend never decides entitlement itself -- it renders
    what this returns, and every dispatch is re-checked server-side anyway
    (see TranscriptionJobViewSet.ai_revisions). Deliberately returns no API
    key, no prompt text, and no provider configuration beyond the model's
    public name.
    """
    authentication_classes = API_AUTH
    permission_classes = [IsAuthenticatedUser]

    def get(self, request):
        return Response(ai_budget.capabilities(), status=status.HTTP_200_OK)


class AIRevisionDetailView(views.APIView):
    """
    One revision with its segments -- what the diff review UI loads, and the
    polling fallback for clients that can't hold a WebSocket open (the
    transcription socket self-closes on completion; see Dashboard.jsx).

    GET only. A revision's texts are results, not inputs: the only mutable
    thing about one is which segments are accepted, and that goes through the
    two review endpoints below.
    """
    authentication_classes = API_AUTH
    permission_classes = [IsAuthenticatedUser]

    def get(self, request, revision_id):
        revision = get_object_or_404(AIRevision, pk=revision_id)
        if not may_access_revision(request.user, revision):
            raise Http404
        return Response(AIRevisionSerializer(revision).data, status=status.HTTP_200_OK)


class AIRevisionQualityReportsView(views.APIView):
    """
    Every quality report for one revision, newest first.

    Separate from AIRevisionDetailView, which carries only the latest, so
    that investigating a regression -- comparing what the gate found on
    iteration one against iteration three -- does not require re-running
    anything, while the common case stays a single small payload.

    GET only, like the detail view above and for the same reason: a report
    is a record of what happened. Nothing may edit one, least of all a
    client, since the whole value of an audit trail is that it was not
    negotiable after the fact.
    """
    authentication_classes = API_AUTH
    permission_classes = [IsAuthenticatedUser]

    def get(self, request, revision_id):
        revision = get_object_or_404(AIRevision, pk=revision_id)
        if not may_access_revision(request.user, revision):
            raise Http404
        reports = revision.quality_reports.all()
        return Response(
            {
                'revision': str(revision.id),
                'quality_status': revision.quality_status,
                'quality_profile': revision.quality_profile,
                'submission_ready': revision.submission_ready,
                'reports': AIQualityReportSerializer(reports, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class AIRevisionSegmentReviewView(views.APIView):
    """
    Accept or reject one segment's AI text.

    Rejecting does not delete anything and does not touch the original
    TranscriptSegment (RULE 3) -- it flips `accepted`, which makes
    AIRevisionSegment.effective_text fall back to `original_text`. So a
    reject is instant, reversible, and leaves the AI's suggestion visible
    beside the original for as long as the revision exists.

    `accepted` is the only writable field, enforced by the serializer's
    read_only_fields: allowing processed_text through here would let a client
    write arbitrary text into a version labelled as the model's output.
    """
    authentication_classes = API_AUTH
    permission_classes = [IsAuthenticatedUser]

    def patch(self, request, revision_id, segment_id):
        segment = get_object_or_404(
            AIRevisionSegment, pk=segment_id, revision_id=revision_id
        )
        accepted = request.data.get('accepted')
        if not isinstance(accepted, bool):
            return Response(
                {'error': 'accepted must be true or false.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        segment.accepted = accepted
        segment.save(update_fields=['accepted'])
        return Response(
            AIRevisionSegmentSerializer(segment).data, status=status.HTTP_200_OK
        )


class AIRevisionReviewView(views.APIView):
    """
    Accept All / Reject All for one revision.

    Body: {"action": "accept_all"} or {"action": "reject_all"}.

    Only rows with changed=True are touched -- an unchanged segment's
    `accepted` value is meaningless (effective_text returns the original
    either way), and updating them would make the "N of M accepted" count
    the UI shows misleading. Done as one bulk UPDATE rather than a loop
    because a long transcript is a few hundred rows and SQLite is
    single-writer here.
    """
    authentication_classes = API_AUTH
    permission_classes = [IsAuthenticatedUser]

    ACTIONS = {'accept_all': True, 'reject_all': False}

    def post(self, request, revision_id):
        revision = get_object_or_404(AIRevision, pk=revision_id)
        if not may_access_revision(request.user, revision):
            raise Http404
        action_name = str(request.data.get('action') or '').strip()
        if action_name not in self.ACTIONS:
            return Response(
                {'error': f"action must be one of: {', '.join(sorted(self.ACTIONS))}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        updated = revision.segments.filter(changed=True).update(
            accepted=self.ACTIONS[action_name]
        )
        logger.info(
            '[AI ASSIST] %s applied to revision %s (%d segment(s)).',
            action_name, revision.id, updated,
        )
        return Response(AIRevisionSerializer(revision).data, status=status.HTTP_200_OK)


class ExportTranscriptView(views.APIView):
    """
    Server-side document generator. Supports TXT, DOCX, SRT, VTT.

    By default exports the original transcript. Pass ?language=<code> to
    export a cached translation instead, ?variant=academic for the
    deterministic academic-notation version, or ?variant=ai&revision=<uuid>
    for an AI Assist revision -- same formatting code either way (see
    core.services.export_formats), only which segments are fed in differs,
    so every variant preserves the same timestamps/speakers/layout as the
    original. `language` and `variant` are mutually exclusive; `language`
    wins if both are somehow passed.
    """
    authentication_classes = API_AUTH
    permission_classes = [IsAuthenticatedUser]

    def _document_title(self, job, suffix=''):
        """
        Uses the user's edited document title when they've set one (see the
        metadata action), falling back to the original generated string.

        The fallback matters: every job that existed before document_metadata
        was added has an empty dict, and every export of those must keep
        producing exactly the header it produced before.
        """
        base = (job.document_metadata or {}).get('title') or f"Transcript Report - Job {job.id}"
        return f"{base}{suffix}"

    def get(self, request, job_id, format_type):
        job = get_object_or_404(TranscriptionJob, id=job_id)
        if not may_access_job(request.user, job):
            # 404, not 403: whether a job exists is itself information.
            raise Http404
        language = request.query_params.get('language')
        variant = request.query_params.get('variant')

        if language:
            # KNOWN AND ACCEPTED GAP -- the offline translation path.
            #
            # `Translation` is produced by run_translation_pipeline using the
            # local NLLB/M2M engines, which never touch AI Assist. The quality
            # gate is only ever invoked from run_ai_revision_pipeline, so
            # nothing here has been audited: no source-fidelity check, no
            # terminology anchoring, no deterministic number comparison.
            #
            # Exporting this as an Eagle submission is therefore possible and
            # is deliberately still allowed, because this path is offline and
            # free and gating it would mean spending Gemini tokens on the one
            # pipeline chosen precisely to avoid them.
            #
            # It is a real gap, not an oversight: text can reach a client
            # through here having passed no quality check whatsoever. If that
            # ever stops being acceptable, the fix is quality fields on
            # Translation plus the same engine call -- see
            # SubmissionRouteAuditTestCase.test_R2_* which pins this behaviour
            # so the decision is revisited deliberately rather than by
            # accident.
            translation = get_object_or_404(Translation, job=job, language=language)
            if translation.status != 'completed':
                return Response(
                    {"error": f"Translation to '{language}' is not complete yet (status: {translation.status})."},
                    status=status.HTTP_409_CONFLICT,
                )
            segments = translation.segments.all().order_by('start_time')
            language_name = TRANSLATION_LANGUAGES.get(language, {}).get('name', language)
            title = self._document_title(job, f" ({language_name} translation)")
            filename_suffix = f"_{language}"
        elif variant == 'academic':
            academic_format = getattr(job, 'academic_format', None)
            if academic_format is None or academic_format.status != 'completed':
                return Response(
                    {"error": "Academic formatting for this job is not complete yet."},
                    status=status.HTTP_409_CONFLICT,
                )
            segments = academic_format.segments.all().order_by('start_time')
            title = self._document_title(job, " (Academic Format)")
            filename_suffix = "_academic"
        elif variant == 'ai':
            # An AI revision can cover a sub-range of the transcript, and its
            # rows carry effective_text (the accept/reject result) rather than
            # a plain `text` attribute the exporters understand -- so this
            # branch adapts, and never mutates the revision's rows.
            revision_id = request.query_params.get('revision')
            if not revision_id:
                return Response(
                    {"error": "revision is required when variant=ai."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            revision = get_object_or_404(AIRevision, pk=revision_id, job=job)
            if revision.status != 'completed':
                return Response(
                    {"error": f"AI revision is not complete yet (status: {revision.status})."},
                    status=status.HTTP_409_CONFLICT,
                )
            segments = [
                SimpleNamespace(
                    speaker_label=segment.speaker_label,
                    start_time=segment.start_time,
                    end_time=segment.end_time,
                    text=segment.effective_text,
                )
                for segment in revision.segments.all().order_by('start_time')
            ]
            title = self._document_title(job, f" ({revision.label})")
            filename_suffix = f"_ai_{revision.operation}"
        else:
            segments = job.segments.all().order_by('start_time')
            title = self._document_title(job)
            filename_suffix = ""

        exporter = export_formats.EXPORTERS.get(format_type)
        if not exporter:
            return Response({"error": "Unsupported export format requested."}, status=status.HTTP_400_BAD_REQUEST)

        render_fn, content_type, extension = exporter
        segments = export_formats.apply_speaker_aliases(segments, job.speaker_aliases)

        meta = dict(job.document_metadata or {})
        for k in ['client', 'transcriber', 'translator', 'date', 'title', 'presenter', 'author', 'source_language', 'target_language']:
            val = request.query_params.get(k)
            if val:
                meta[k] = val

        # ------------------------------------------------------------------
        # SUBMISSION GATE.
        #
        # The 'eagle' format is not just another renderer -- it is the
        # submission surface, the one whose output goes to a paying client.
        # So it is the one place that refuses text the quality gate rejected.
        #
        # Scoped deliberately, on all three axes:
        #
        #   * Only the eagle format. txt/docx/srt still export a refused
        #     revision, because a reviewer usually needs the document in
        #     order to FIX it, and trapping the work helps nobody.
        #   * Only an actual refusal (QUALITY_REFUSED_STATUSES). A revision
        #     with quality_status='not_run' exports exactly as it always
        #     has -- the gate is opt-in, so refusing ungated runs would
        #     block essentially every existing revision.
        #   * Only the AI-revision variant. The offline translation path
        #     (?language=) is knowingly NOT gated -- see the note below.
        #
        # A 409 rather than a 403: the state is wrong, not the caller.
        # ------------------------------------------------------------------
        if format_type == 'eagle' and variant == 'ai':
            revision_obj = locals().get('revision')
            if revision_obj is not None and revision_obj.quality_status in AIRevision.QUALITY_REFUSED_STATUSES:
                logger.warning(
                    '[AI QUALITY] Refused Eagle export of revision %s (%s).',
                    revision_obj.id, revision_obj.quality_status,
                )
                if revision_obj.quality_status == 'technical_failure':
                    detail = (
                        'The quality pipeline could not check this version, so '
                        'it has not been cleared for submission.'
                    )
                else:
                    detail = (
                        revision_obj.quality_reason
                        or 'Unresolved quality issues remain.'
                    )
                return Response(
                    {
                        'error': (
                            'This version has not passed quality review and '
                            'cannot be exported as an Eagle Web submission. '
                            + detail
                        ),
                        'quality_status': revision_obj.quality_status,
                        'critical_issue_count': revision_obj.critical_issue_count,
                        'high_issue_count': revision_obj.high_issue_count,
                        # Named so the client can offer the obvious next step
                        # rather than leaving the user stuck at a refusal.
                        'alternatives': ['txt', 'docx'],
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        try:
            with profile_stage("export", job=job_id, format=format_type):
                if format_type == 'eagle':
                    is_translation_export = bool(
                        language or (variant == 'ai' and locals().get('revision') and getattr(revision, 'operation', None) == 'translate')
                    )
                    if is_translation_export:
                        src_code = (locals().get('revision') and revision.source_language) or job.detected_language or job.language or 'en'
                        tgt_code = language or (locals().get('revision') and revision.target_language) or 'fr'
                        src_name = TRANSLATION_LANGUAGES.get(src_code, {}).get('name', src_code.title() if src_code else 'English')
                        tgt_name = TRANSLATION_LANGUAGES.get(tgt_code, {}).get('name', tgt_code.title() if tgt_code else 'French')
                        content = export_formats.to_eagle_translation(
                            segments,
                            title=meta.get('title') or (job.document_metadata or {}).get('title') or f"Translation Job - {job.id}",
                            source_lang=meta.get('source_language') or src_name,
                            target_lang=meta.get('target_language') or tgt_name,
                            metadata=meta,
                        )
                    else:
                        calc_duration = max((float(s.end_time) for s in segments), default=0.0)
                        content = export_formats.to_eagle_transcription(
                            segments,
                            title=meta.get('title') or (job.document_metadata or {}).get('title') or f"Transcription Job - {job.id}",
                            diarization_enabled=job.enable_diarization,
                            metadata=meta,
                            total_duration=calc_duration,
                        )
                else:
                    content = render_fn(segments, title, diarization_enabled=job.enable_diarization)
        except ImportError as e:
            return Response({"error": f"Required export package is not installed on server: {e}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        response = HttpResponse(content, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="transcript_{job.id}{filename_suffix}.{extension}"'
        return response


class ExportSeriesView(views.APIView):
    """
    Convenience-only bulk download: packages every completed series item's
    individual export into one ZIP, using the exact same per-item
    rendering as ExportTranscriptView. Never merges transcripts, never
    rewrites timestamps -- each file in the archive is byte-identical to
    what exporting that one job directly would produce. Items that aren't
    completed yet are simply skipped, not treated as an error.
    """
    authentication_classes = API_AUTH
    permission_classes = [IsAuthenticatedUser]

    def get(self, request, series_id, format_type):
        jobs = list(
            TranscriptionJob.objects.filter(series_id=series_id, status='completed').order_by('series_index')
        )
        if not jobs:
            return Response(
                {"error": "No completed items found for this series yet."}, status=status.HTTP_404_NOT_FOUND,
            )

        exporter = export_formats.EXPORTERS.get(format_type)
        if not exporter:
            return Response({"error": "Unsupported export format requested."}, status=status.HTTP_400_BAD_REQUEST)
        render_fn, _content_type, extension = exporter

        buffer = io.BytesIO()
        with profile_stage("export_series", series=str(series_id), format=format_type, item_count=len(jobs)):
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                used_names = set()
                for job in jobs:
                    segments = job.segments.all().order_by('start_time')
                    segments = export_formats.apply_speaker_aliases(segments, job.speaker_aliases)
                    title = job.series_title or f"Item {(job.series_index or 0) + 1}"
                    try:
                        content = render_fn(segments, f"Transcript Report - {title}", diarization_enabled=job.enable_diarization)
                    except ImportError:
                        continue  # skip this item rather than fail the whole archive

                    safe_title = re.sub(r'[\\/*?:"<>|]', '_', title).strip()[:100] or str(job.id)
                    filename = f"{(job.series_index or 0) + 1:02d}_{safe_title}.{extension}"
                    # Guard against two items producing the same sanitized
                    # filename (e.g. identical titles) silently overwriting
                    # each other in the archive.
                    if filename in used_names:
                        filename = f"{(job.series_index or 0) + 1:02d}_{safe_title}_{job.id}.{extension}"
                    used_names.add(filename)
                    zf.writestr(filename, content)

        response = HttpResponse(buffer.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="series_{series_id}_{format_type}.zip"'
        return response
