import os
import time
import logging
from typing import Dict
import threading
from django.utils import timezone

# Safe Celery Import
try:
    from celery import shared_task
except Exception:
    # Fallback decorator that runs the task inside a background thread
    def shared_task(func):
        def delay(*args, **kwargs):
            import threading
            thread = threading.Thread(target=func, args=args, kwargs=kwargs)
            thread.daemon = True
            thread.start()
            return thread
        func.delay = delay
        return func

# Safe Channels Import
try:
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync
    HAS_CHANNELS = True
except ImportError:
    HAS_CHANNELS = False

from .models import (
    TranscriptionJob, TranscriptSegment, Translation, TranslatedSegment, DiscoveryTask,
    AcademicFormat, AcademicSegment, AIRevision, AIRevisionSegment,
)
try:
    from backend.core.services.ai_pipeline import AudioExtractor, JobCancelledError
    from backend.core.services.languages import is_supported
    from backend.core.services.engines import EngineRegistry
    from backend.core.services.translation import (
        TranslationEngineRegistry, batch_by_size, build_fast_engine, MarkerIntegrityError,
    )
    from backend.core.services import series_discovery
    from backend.core.services.profiling import profile_stage
    from backend.core.services.diarization import (
        SpeakerDiarizer, assign_speaker_for_interval, build_stable_speaker_labels,
    )
    from backend.core.services.academic import SubjectDetector, AcademicFormatterRegistry, validate as academic_validate
    from backend.core.services.ai_assist import AIAssistError, AIPostProcessor, TokenUsage, int_config
except ModuleNotFoundError:
    from core.services.ai_pipeline import AudioExtractor, JobCancelledError
    from core.services.languages import is_supported
    from core.services.engines import EngineRegistry
    from core.services.translation import (
        TranslationEngineRegistry, batch_by_size, build_fast_engine, MarkerIntegrityError,
    )
    from core.services import series_discovery
    from core.services.profiling import profile_stage
    from core.services.diarization import (
        SpeakerDiarizer, assign_speaker_for_interval, build_stable_speaker_labels,
    )
    from core.services.academic import SubjectDetector, AcademicFormatterRegistry, validate as academic_validate
    from core.services.ai_assist import AIAssistError, AIPostProcessor, TokenUsage, int_config

logger = logging.getLogger(__name__)

# Bounded concurrency semaphore for Gemini AI Assist jobs to prevent burst rate limits
AI_CONCURRENCY_SEMAPHORE = threading.BoundedSemaphore(
    int_config('AI_MAX_CONCURRENT_REQUESTS', 2)
)

def broadcast_update(job_id, data):
    if HAS_CHANNELS:
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f'transcription_{job_id}',
                    {
                        'type': 'transcription_update',
                        'data': data
                    }
                )
        except Exception as e:
            logger.warning(f"Failed to send to channel layer: {e}")
    
    # Also log/print for local visibility or debugging
    logger.info(f"[BROADCAST UPDATE] Job {job_id}: {data}")

def _cancel_check_for(job_id):
    """
    Returns a zero-arg callable that does a fresh, lightweight DB read of
    cancel_requested each time it's called -- deliberately not a cached
    flag, since the cancel HTTP request lands on a different thread/request
    than the one running this pipeline. Passed into ai_pipeline so the
    download subprocess can be polled for cancellation while it runs, and
    checked directly at every stage boundary and inside the transcription
    loop below.
    """
    def check():
        import os
        old_val = os.environ.get('DJANGO_ALLOW_ASYNC_UNSAFE')
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
        try:
            return TranscriptionJob.objects.filter(id=job_id, cancel_requested=True).exists()
        except Exception:
            return False
        finally:
            if old_val is None:
                os.environ.pop('DJANGO_ALLOW_ASYNC_UNSAFE', None)
            else:
                os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = old_val
    return check


def _mark_cancelled(job, job_id, temp_wav_path):
    logger.info(f"Job {job_id} cancelled.")
    job.status = 'cancelled'
    job.save()
    broadcast_update(job_id, {
        'type': 'status_update',
        'status': 'cancelled',
        'message': 'Transcription cancelled.',
    })


@shared_task
def run_transcription_pipeline(job_id):
    logger.info(f"Starting transcription pipeline for Job: {job_id}")
    try:
        job = TranscriptionJob.objects.get(id=job_id)
    except TranscriptionJob.DoesNotExist:
        logger.error(f"Job {job_id} does not exist.")
        return

    temp_wav_path = os.path.abspath(f"job_{job_id}_temp.wav")
    cancel_check = _cancel_check_for(job_id)

    try:
        if cancel_check():
            _mark_cancelled(job, job_id, temp_wav_path)
            return

        # 1. Update status to Downloading
        job.status = 'downloading'
        job.save()
        broadcast_update(job_id, {
            'type': 'status_update',
            'status': 'downloading',
            'message': 'Downloading media and extracting audio...'
        })

        # 2. Extract Audio
        success = False
        upload_error: Dict[str, str] = {}
        with profile_stage("download_extract", job=job_id, source=job.input_source):
            if job.input_source == 'url':
                success = AudioExtractor.download_and_extract_url(
                    job.source_url, temp_wav_path, extra_headers=job.site_auth_headers,
                    cancel_check=cancel_check,
                )
            elif job.input_source == 'upload':
                if job.file_path and os.path.exists(job.file_path):
                    success = AudioExtractor.extract_from_file(
                        job.file_path, temp_wav_path, cancel_check=cancel_check, error_detail=upload_error,
                    )
                else:
                    logger.error(f"Upload file path missing or not found for job {job.id}: {job.file_path}")
                    upload_error['detail'] = 'The uploaded file could not be found on the server.'
                    success = False

        if not success:
            if job.input_source == 'upload':
                # FFmpeg's stderr is usually a multi-line probe dump; the
                # last non-empty line is normally the actual failure reason
                # (e.g. "Invalid data found when processing input" for a
                # corrupt/truncated file, or "Unknown decoder ..." for an
                # unsupported codec) -- short enough to show the user
                # directly instead of a generic, unhelpful message.
                lines = [l.strip() for l in upload_error.get('detail', '').splitlines() if l.strip()]
                reason = lines[-1] if lines else 'the file may be corrupt, empty, or use an unsupported codec.'
                raise RuntimeError(f"Could not process the uploaded file: {reason}")
            raise RuntimeError("Audio extraction or download failed.")

        if cancel_check():
            _mark_cancelled(job, job_id, temp_wav_path)
            return

        # 3. Diarize (opt-in only -- see core.services.diarization). Runs as
        # one whole-file pass BEFORE transcription starts, not interleaved
        # with it: diarization needs full-file context to cluster speakers,
        # so there's no way to stream it chunk-by-chunk the way Whisper
        # output streams. This adds a one-time delay before the FIRST
        # segment appears when enabled, but does not change the streaming
        # behavior of transcription itself once it starts -- segments still
        # get produced and broadcast one at a time exactly as before.
        #
        # Broadcasts its own 'diarizing' status (a STATUS_CHOICES entry
        # that already existed on the model but was never actually used --
        # confirmed live: a real diarization pass over a longer file
        # genuinely takes several minutes of real CPU-only model inference,
        # and until this fix the UI just showed "Transcribing speech to
        # text..." the whole time, with zero indication anything was
        # happening or why it was slower than a non-diarized job -- looked
        # indistinguishable from being stuck. Only broadcast when
        # enable_diarization is actually True; jobs that don't use it never
        # see this status at all, unchanged from before.
        #
        # diarization_turns stays None whenever: the checkbox is off, the
        # library/model/token isn't configured, or diarization fails for
        # any reason -- in every one of those cases the loop below leaves
        # speaker_label empty rather than fabricating one (see the removed
        # 15-second alternating placeholder below).
        # 3. Update status to Transcribing (Whisper runs first at C++ int8 speed ~30s)
        if cancel_check():
            _mark_cancelled(job, job_id, temp_wav_path)
            return
        job.status = 'transcribing'
        job.save()
        broadcast_update(job_id, {
            'type': 'status_update',
            'status': 'transcribing',
            'message': 'Transcribing speech to text...'
        })

        # 4. Transcribe
        requested_language = job.language if job.language and is_supported(job.language) else None
        with profile_stage("engine_select_and_load", job=job_id, language=requested_language or "auto"):
            transcriber = EngineRegistry.select(requested_language)

        segment_count = 0
        saved_segments = []
        cancelled_mid_transcription = False
        with profile_stage("transcription", job=job_id):
            for chunk in transcriber.transcribe(temp_wav_path, enable_vad=True, language=requested_language):
                if cancel_check():
                    cancelled_mid_transcription = True
                    break

                initial_speaker = 'Speaker 1' if job.enable_diarization else ''
                segment = TranscriptSegment.objects.create(
                    job=job,
                    speaker_label=initial_speaker,
                    start_time=chunk['start'],
                    end_time=chunk['end'],
                    text=chunk['text'],
                    confidence=chunk['confidence']
                )
                saved_segments.append(segment)

                broadcast_update(job_id, {
                    'type': 'segment_added',
                    'segment': {
                        'id': str(segment.id),
                        'speaker_label': initial_speaker,
                        'start_time': chunk['start'],
                        'end_time': chunk['end'],
                        'text': chunk['text'],
                        'confidence': chunk['confidence']
                    }
                })
                segment_count += 1

        if cancelled_mid_transcription:
            _mark_cancelled(job, job_id, temp_wav_path)
            return

        # 5. Diarize segments (opt-in fast Whisper-aligned diarization)
        if job.enable_diarization and saved_segments:
            if cancel_check():
                _mark_cancelled(job, job_id, temp_wav_path)
                return
            job.status = 'diarizing'
            job.save()
            broadcast_update(job_id, {
                'type': 'status_update',
                'status': 'diarizing',
                'message': 'Identifying speakers...'
            })
            with profile_stage("diarization", job=job_id):
                input_dicts = [
                    {'id': seg.id, 'start': seg.start_time, 'end': seg.end_time, 'text': seg.text}
                    for seg in saved_segments
                ]
                diarized_dicts = SpeakerDiarizer.diarize_segments(
                    temp_wav_path, input_dicts, expected_speakers=job.expected_speakers,
                )
                updated_payload = []
                for d in diarized_dicts:
                    spk = d.get('speaker_label', '')
                    TranscriptSegment.objects.filter(id=d['id']).update(speaker_label=spk)
                    updated_payload.append({
                        'id': str(d['id']),
                        'speaker_label': spk,
                        'start_time': d['start'],
                        'end_time': d['end'],
                        'text': d['text']
                    })
                # CARRY THE UNCERTAINTY FORWARD.
                #
                # Male and female F0 ranges genuinely overlap, so a speaker
                # near the boundary is a coin flip. Measured on a real
                # lecture: an adult male read 178 Hz against a 165 Hz
                # boundary and went out as Female_1 on all 432 lines, with
                # the doubt recorded only in a log line nobody reads.
                #
                # Stored on the job so the review screen can ask the one
                # person who can settle it in five seconds -- before they
                # spend the hour editing text under the wrong label.
                uncertain = list(getattr(SpeakerDiarizer,
                                         'last_uncertain_speakers', []) or [])
                if uncertain:
                    metadata = dict(job.document_metadata or {})
                    metadata['uncertain_speakers'] = uncertain
                    TranscriptionJob.objects.filter(pk=job.pk).update(
                        document_metadata=metadata)
                    logger.warning(
                        '[DIARIZATION] %d speaker(s) need a human ear: %s',
                        len(uncertain),
                        ', '.join(u['label'] for u in uncertain))

                broadcast_update(job_id, {
                    'type': 'segments_updated',
                    'segments': updated_payload
                })

        if cancelled_mid_transcription:
            # Transcript segments produced before the cancel point are kept
            # (not a partial/broken state -- each one is a complete, valid
            # segment), matching "do not delete completed transcript data
            # unless explicitly requested."
            _mark_cancelled(job, job_id, temp_wav_path)
            return

        # 6. Complete Job
        if transcriber.last_info is not None:
            job.detected_language = transcriber.last_info.language
            job.language_confidence = transcriber.last_info.language_probability
            broadcast_update(job_id, {
                'type': 'language_detected',
                'detected_language': job.detected_language,
                'language_confidence': job.language_confidence,
            })

        job.status = 'completed'
        job.completed_at = timezone.now()
        job.save()

        broadcast_update(job_id, {
            'type': 'status_update',
            'status': 'completed',
            'message': 'Transcription successfully completed.'
        })

    except JobCancelledError:
        _mark_cancelled(job, job_id, temp_wav_path)

    except Exception as e:
        logger.error(f"Error in transcription pipeline for Job {job_id}: {str(e)}", exc_info=True)
        job.status = 'failed'
        job.error_message = str(e)
        job.save()

        broadcast_update(job_id, {
            'type': 'status_update',
            'status': 'failed',
            'message': f"Failed: {str(e)}"
        })

    finally:
        # Cleanup
        if os.path.exists(temp_wav_path):
            try:
                os.remove(temp_wav_path)
            except Exception:
                pass
        # Uploaded jobs get a second, separate temp file: the ORIGINAL
        # upload, written directly to its own OS-temp path by
        # TranscriptionJobViewSet.create() (see views.py) -- entirely
        # independent of temp_wav_path above. It's only ever read here via
        # extract_from_file(), never needed again after this pipeline run
        # ends, so it's removed unconditionally in this same finally block
        # -- covering completed, failed, and cancelled (including every
        # early-return cancellation point above) in one place.
        if job.input_source == 'upload' and job.file_path and os.path.exists(job.file_path):
            try:
                os.remove(job.file_path)
            except Exception:
                logger.warning(f"Job {job_id}: could not remove uploaded temp file '{job.file_path}'.")


def run_discovery_task(task_id):
    """
    Runs series_discovery.discover() in the background so the HTTP request
    that requested it (DiscoverSeriesView.post) can return immediately
    instead of blocking for however long the slowest discovery strategy
    takes. The frontend polls DiscoveryTask by id and shows the exact same
    series-choice UI once status flips to completed -- this only changes
    the transport, never the discovery logic or the UX it drives.
    """
    try:
        task = DiscoveryTask.objects.get(id=task_id)
    except DiscoveryTask.DoesNotExist:
        logger.error(f"DiscoveryTask {task_id} does not exist.")
        return

    try:
        with profile_stage("discovery", task=task_id, url=task.source_url):
            result = series_discovery.discover(
                task.source_url, extra_headers=task.site_auth_headers, try_sequential=task.try_sequential,
            )
        task.result = result
        task.status = 'completed'
        task.completed_at = timezone.now()
        task.save(update_fields=['result', 'status', 'completed_at'])
    except Exception as e:
        logger.error(f"Error in discovery task {task_id}: {str(e)}", exc_info=True)
        task.status = 'failed'
        task.error_message = str(e)
        task.completed_at = timezone.now()
        task.save(update_fields=['status', 'error_message', 'completed_at'])


def run_series_pipeline(series_id):
    """
    Processes every job in a discovered series/playlist strictly one at a
    time, in series_index order. Deliberately sequential rather than
    concurrent: it preserves ordering (a stated requirement) and avoids the
    CPU contention multiple concurrent heavy transcription engines cause
    when run in parallel via Python threads.

    Resumable by construction: this can be called again for the same
    series_id (e.g. after a server restart) and it will skip any job whose
    status is already 'completed', picking up wherever it left off.
    """
    jobs = list(TranscriptionJob.objects.filter(series_id=series_id).order_by('series_index'))
    logger.info(f"Starting series pipeline for series {series_id}: {len(jobs)} item(s)")

    for job in jobs:
        job.refresh_from_db()
        if job.status == 'completed':
            logger.info(f"Series {series_id}: skipping already-completed item {job.series_index} ({job.id})")
            continue
        logger.info(f"Series {series_id}: processing item {job.series_index} ({job.id})")
        run_transcription_pipeline(job.id)

    logger.info(f"Series pipeline for series {series_id} finished.")


# Fast Translation Mode (see core.services.translation.llm_fast_engine) works
# on much larger chunks than the standard engine batches, since one LLM call
# can hold far more context than M2M100/NLLB's ~1024-token window -- this is
# what collapses many batches into a handful of calls. Only relevant when
# build_fast_engine() returns a configured engine; otherwise batch_by_size's
# own default (~20-50 segments) is used, unchanged.
FAST_MODE_MAX_CHARS = 12000  # ~1500-3000 words


def _translate_chunk(chunk, engine, source_lang, target_lang, fast_engine):
    """
    Translates one chunk of segments, returning translated texts in the
    same order (length always matches `chunk` -- enforced below, since a
    mismatch here would silently misalign the timeline when zipped back
    onto the original segments). Tries fast_engine first when configured;
    a network failure or a broken segment-marker mapping falls back to the
    standard engine on normal-sized sub-batches for this chunk only, never
    for the rest of the job.
    """
    texts = [seg.text for seg in chunk]

    if fast_engine is not None:
        try:
            with profile_stage("translate_fast_chunk", size=len(texts)):
                translated = fast_engine.translate_batch(texts, source_lang, target_lang)
            if len(translated) == len(chunk):
                return translated
            logger.warning(
                f"Fast translation mode returned {len(translated)} segments for a "
                f"{len(chunk)}-segment chunk; falling back to the standard batch engine."
            )
        except Exception as e:
            logger.warning(
                f"Fast translation mode failed for a {len(texts)}-segment chunk ({e}); "
                f"falling back to the standard batch engine for this chunk."
            )

    translated = []
    for sub_chunk in batch_by_size(chunk, get_text=lambda s: s.text):
        sub_texts = [s.text for s in sub_chunk]
        with profile_stage("translate_batch", size=len(sub_texts)):
            translated.extend(engine.translate_batch(sub_texts, source_lang, target_lang))

    if len(translated) != len(chunk):
        raise RuntimeError(
            f"Translation engine returned {len(translated)} results for {len(chunk)} segments; "
            "refusing to guess at a broken timeline mapping."
        )
    return translated


def run_translation_pipeline(translation_id):
    """
    Translates a job's transcript into one target language, in batches of
    ~20-50 segments per model call (or, if Fast Translation Mode is
    configured, a handful of much larger LLM calls) rather than one call per
    segment. Deliberately separate from run_transcription_pipeline -- only
    ever invoked when a user explicitly requests a translation, never
    automatically and never as part of transcribing. The original
    TranscriptSegment rows are read-only here: each TranslatedSegment copies
    its start_time/end_time/speaker_label from the source segment and only
    ever changes the text. Batching only changes how many segments are
    translated per call -- each batch's translated texts are always zipped
    back onto the SAME segments in the SAME order, one-to-one, so timing and
    speakers are preserved exactly regardless of batch size.
    """
    logger.info(f"Starting translation pipeline for Translation: {translation_id}")
    try:
        translation = Translation.objects.select_related('job').get(id=translation_id)
    except Translation.DoesNotExist:
        logger.error(f"Translation {translation_id} does not exist.")
        return

    job_id = translation.job_id

    try:
        translation.status = 'translating'
        translation.save()
        broadcast_update(job_id, {
            'type': 'translation_status_update',
            'translation_id': str(translation.id),
            'language': translation.language,
            'status': 'translating',
        })

        source_lang = translation.job.detected_language or translation.job.language
        engine = TranslationEngineRegistry.select(translation.language, source_language=source_lang)
        fast_engine = build_fast_engine()

        segments = list(translation.job.segments.all().order_by('start_time'))
        if fast_engine is not None:
            chunks = batch_by_size(segments, get_text=lambda s: s.text,
                                    max_items=10**9, max_chars=FAST_MODE_MAX_CHARS)
        else:
            chunks = batch_by_size(segments, get_text=lambda s: s.text)

        def translation_cancelled():
            return Translation.objects.filter(id=translation.id, cancel_requested=True).exists()

        def mark_translation_cancelled():
            logger.info(f"Translation {translation.id} cancelled.")
            translation.status = 'cancelled'
            translation.save()
            broadcast_update(job_id, {
                'type': 'translation_status_update',
                'translation_id': str(translation.id),
                'language': translation.language,
                'status': 'cancelled',
            })

        for chunk in chunks:
            # Checked between batches rather than mid-batch: each
            # translate_batch() call is one in-process/HTTP call with no
            # safe interruption point, but batches are small enough
            # (~20-50 segments, or a few thousand chars in Fast Mode) that
            # "stops before the next batch" is effectively immediate.
            if translation_cancelled():
                mark_translation_cancelled()
                return

            translated_texts = _translate_chunk(chunk, engine, source_lang, translation.language, fast_engine)

            translated_segments = [
                TranslatedSegment(
                    translation=translation,
                    original_segment=seg,
                    speaker_label=seg.speaker_label,
                    start_time=seg.start_time,
                    end_time=seg.end_time,
                    text=text,
                )
                for seg, text in zip(chunk, translated_texts)
            ]
            TranslatedSegment.objects.bulk_create(translated_segments)

            broadcast_update(job_id, {
                'type': 'translated_batch_added',
                'translation_id': str(translation.id),
                'language': translation.language,
                'segments': [
                    {
                        'id': str(ts.id),
                        'speaker_label': ts.speaker_label,
                        'start_time': ts.start_time,
                        'end_time': ts.end_time,
                        'text': ts.text,
                    }
                    for ts in translated_segments
                ],
            })

        # A cancel requested during the final batch's translate_batch() call
        # has no further loop iteration to catch it -- check once more here
        # so "cancel while translating the last batch" still lands on
        # 'cancelled' (with that batch's results already persisted) rather
        # than silently completing as if nothing happened.
        if translation_cancelled():
            mark_translation_cancelled()
            return

        translation.status = 'completed'
        translation.completed_at = timezone.now()
        translation.save()

        broadcast_update(job_id, {
            'type': 'translation_status_update',
            'translation_id': str(translation.id),
            'language': translation.language,
            'status': 'completed',
        })

    except Exception as e:
        logger.error(f"Error in translation pipeline for Translation {translation_id}: {str(e)}", exc_info=True)
        translation.status = 'failed'
        translation.error_message = str(e)
        translation.save()

        broadcast_update(job_id, {
            'type': 'translation_status_update',
            'translation_id': str(translation.id),
            'language': translation.language,
            'status': 'failed',
            'message': f"Failed: {str(e)}",
        })


def run_academic_format_pipeline(academic_format_id):
    """
    Converts a job's already-completed transcript into academic notation
    ("x squared" -> "x²") -- a separate, on-demand, cached pass, never part
    of run_transcription_pipeline and never re-running ASR/diarization/
    translation. Same architectural shape as run_translation_pipeline
    (fetch row -> stage loop -> broadcast per stage -> except/finally), with
    three stages instead of one: detecting -> formatting -> validating.

    Operates entirely on TranscriptSegment.text as already produced by ASR
    -- this function is the ONLY thing in the whole codebase that imports
    core.services.academic, and it never touches AudioExtractor, the
    Whisper/MMS engines, or SpeakerDiarizer. The original TranscriptSegment
    rows are read-only here, exactly like Translation treats them: each
    AcademicSegment copies start_time/end_time/speaker_label from its
    source segment and only ever produces different text.
    """
    logger.info(f"Starting academic format pipeline for AcademicFormat: {academic_format_id}")
    try:
        academic_format = AcademicFormat.objects.select_related('job').get(id=academic_format_id)
    except AcademicFormat.DoesNotExist:
        logger.error(f"AcademicFormat {academic_format_id} does not exist.")
        return

    job_id = academic_format.job_id

    def academic_format_cancelled():
        return AcademicFormat.objects.filter(id=academic_format.id, cancel_requested=True).exists()

    def mark_academic_format_cancelled():
        logger.info(f"AcademicFormat {academic_format.id} cancelled.")
        academic_format.status = 'cancelled'
        academic_format.save()
        broadcast_update(job_id, {
            'type': 'academic_format_status_update',
            'academic_format_id': str(academic_format.id),
            'status': 'cancelled',
        })

    try:
        if academic_format_cancelled():
            mark_academic_format_cancelled()
            return

        segments = list(academic_format.job.segments.all().order_by('start_time'))

        academic_format.status = 'detecting'
        academic_format.save()
        broadcast_update(job_id, {
            'type': 'academic_format_status_update',
            'academic_format_id': str(academic_format.id),
            'status': 'detecting',
            'message': 'Detecting subject...',
        })

        subject, confidence = SubjectDetector.detect(segments)
        academic_format.subject = subject
        academic_format.subject_confidence = confidence
        academic_format.save()
        logger.info(f"AcademicFormat {academic_format.id}: detected subject={subject} confidence={confidence}")

        if academic_format_cancelled():
            mark_academic_format_cancelled()
            return

        academic_format.status = 'formatting'
        academic_format.save()
        broadcast_update(job_id, {
            'type': 'academic_format_status_update',
            'academic_format_id': str(academic_format.id),
            'status': 'formatting',
            'message': 'Applying academic notation...',
            'subject': subject,
            'subject_confidence': confidence,
        })

        formatter = AcademicFormatterRegistry.select(subject)
        created_segments = []
        for seg in segments:
            if academic_format_cancelled():
                mark_academic_format_cancelled()
                return

            formatted_text, was_modified = formatter.format_segment(seg.text)
            academic_seg = AcademicSegment.objects.create(
                academic_format=academic_format,
                original_segment=seg,
                speaker_label=seg.speaker_label,
                start_time=seg.start_time,
                end_time=seg.end_time,
                text=formatted_text,
                was_modified=was_modified,
            )
            created_segments.append(academic_seg)

            broadcast_update(job_id, {
                'type': 'academic_segment_added',
                'academic_format_id': str(academic_format.id),
                'segment': {
                    'id': str(academic_seg.id),
                    'speaker_label': academic_seg.speaker_label,
                    'start_time': academic_seg.start_time,
                    'end_time': academic_seg.end_time,
                    'text': academic_seg.text,
                    'was_modified': academic_seg.was_modified,
                },
            })

        if academic_format_cancelled():
            mark_academic_format_cancelled()
            return

        academic_format.status = 'validating'
        academic_format.save()
        broadcast_update(job_id, {
            'type': 'academic_format_status_update',
            'academic_format_id': str(academic_format.id),
            'status': 'validating',
            'message': 'Validating...',
        })

        # The original transcript is always the source of truth (ticket
        # §12): a segment whose formatted text doesn't pass validation
        # falls back to its ORIGINAL text -- never fails the whole job,
        # exactly mirroring how a formatter's own conservatism works (see
        # core.services.academic.validator's own docstring).
        reverted_count = 0
        for academic_seg in created_segments:
            if not academic_seg.was_modified:
                continue
            is_valid, reason = academic_validate(academic_seg.original_segment.text, academic_seg.text)
            if not is_valid:
                logger.warning(
                    f"AcademicFormat {academic_format.id}: reverting segment {academic_seg.id} "
                    f"to its original text ({reason})."
                )
                academic_seg.text = academic_seg.original_segment.text
                academic_seg.was_modified = False
                academic_seg.save(update_fields=['text', 'was_modified'])
                reverted_count += 1

        academic_format.status = 'completed'
        academic_format.completed_at = timezone.now()
        academic_format.save()

        broadcast_update(job_id, {
            'type': 'academic_format_status_update',
            'academic_format_id': str(academic_format.id),
            'status': 'completed',
            'subject': subject,
            'subject_confidence': confidence,
        })
        logger.info(
            f"AcademicFormat {academic_format.id} completed: {len(created_segments)} segment(s), "
            f"{reverted_count} reverted by validation."
        )

    except Exception as e:
        logger.error(f"Error in academic format pipeline for AcademicFormat {academic_format_id}: {str(e)}", exc_info=True)
        academic_format.status = 'failed'
        academic_format.error_message = str(e)
        academic_format.save()

        broadcast_update(job_id, {
            'type': 'academic_format_status_update',
            'academic_format_id': str(academic_format.id),
            'status': 'failed',
            'message': f"Failed: {str(e)}",
        })


def _ai_language_name(language_code):
    """Human-readable language name for the prompt. Models translate more
    reliably into 'Swahili' than into 'sw'."""
    if not language_code:
        return None
    try:
        from backend.core.services.languages import PLATFORM_LANGUAGES
    except ModuleNotFoundError:
        from core.services.languages import PLATFORM_LANGUAGES
    entry = PLATFORM_LANGUAGES.get(language_code) or {}
    return entry.get('name') or language_code


def run_ai_revision_pipeline(revision_id):
    """
    Runs one AI Assist operation over a range of a job's completed transcript
    and stores the result as a new version -- never as an edit.

    Same architectural shape as run_academic_format_pipeline above (fetch row
    -> status broadcast -> per-segment create + broadcast -> except/finally),
    and the same hard guarantees: the original TranscriptSegment rows are
    read-only here, every AIRevisionSegment copies start_time/end_time/
    speaker_label from its source segment rather than recomputing them, and
    nothing in this function touches AudioExtractor, the Whisper/MMS engines,
    or SpeakerDiarizer.

    Two things it does differently from the academic pipeline, both because a
    paid external provider is involved:

    1. ERRORS ARE REDACTED. The academic pipeline stores str(e) in
       error_message, which is fine for a local formatter. A provider
       exception can carry the request URL, quota internals, or echoed prompt
       text, and error_message is serialized straight to the browser -- so
       only AIAssistError.safe_message is stored, and the full detail goes to
       the log. An unexpected non-AIAssistError becomes a generic message for
       the same reason: an unredacted traceback must never reach a client.
    2. USAGE IS METERED PER BATCH. Each batch's token counts are appended to
       the AIUsageRecord ledger as they complete, not summed up and written
       once at the end. A run that is cancelled or dies halfway has still
       really spent those tokens, and a budget that forgets them is not a
       budget.
    """
    from . import ai_budget

    logger.info(f"Starting AI revision pipeline for AIRevision: {revision_id}")
    try:
        revision = AIRevision.objects.select_related('job').get(id=revision_id)
    except AIRevision.DoesNotExist:
        logger.error(f"AIRevision {revision_id} does not exist.")
        return

    job_id = revision.job_id

    def revision_cancelled():
        # Fresh DB read every time, exactly like academic_format_cancelled:
        # the flag is set by a different thread handling the cancel request,
        # so the in-memory instance never sees it.
        return AIRevision.objects.filter(id=revision.id, cancel_requested=True).exists()

    def mark_revision_cancelled():
        logger.info(f"AIRevision {revision.id} cancelled.")
        revision.status = 'cancelled'
        revision.save()
        settle_quality_status('Cancelled before the quality gate completed.')
        broadcast_update(job_id, {
            'type': 'ai_revision_status_update',
            'revision_id': str(revision.id),
            'status': 'cancelled',
        })

    def settle_quality_status(reason):
        """
        Moves a revision out of an in-progress quality state when the run
        ends without the gate reporting.

        Without this, a revision that fails or is cancelled mid-audit keeps
        quality_status='auditing' forever -- and an in-progress state that
        never resolves is worse than the ambiguity it replaced, because it
        looks like work still happening.
        """
        if revision.quality_status in AIRevision.QUALITY_IN_PROGRESS_STATUSES:
            revision.quality_status = 'technical_failure'
            revision.quality_reason = reason
            revision.save(update_fields=['quality_status', 'quality_reason'])

    def fail(safe_message):
        revision.status = 'failed'
        revision.error_message = safe_message
        revision.save()
        settle_quality_status(
            'The run failed before the quality gate could report.'
        )
        broadcast_update(job_id, {
            'type': 'ai_revision_status_update',
            'revision_id': str(revision.id),
            'status': 'failed',
            'message': safe_message,
        })

    try:
        if revision_cancelled():
            mark_revision_cancelled()
            return

        segments = revision.source_segments()
        if not segments:
            logger.warning(f"AIRevision {revision.id}: selected range contains no segments.")
            fail('The selected range contains no transcript segments.')
            return

        revision.status = 'processing'
        revision.save()
        broadcast_update(job_id, {
            'type': 'ai_revision_status_update',
            'revision_id': str(revision.id),
            'status': 'processing',
            'message': 'Sending transcript to the AI service...',
            'total_segments': len(segments),
        })

        processor = AIPostProcessor()
        target_language_name = _ai_language_name(revision.target_language)

        usage_total = TokenUsage()
        degraded = False
        processed_count = 0
        cache_hits_total = 0
        cache_misses_total = 0
        model_used = ''
        tokens_saved_total = 0

        with AI_CONCURRENCY_SEMAPHORE:
            for batch in processor.iter_batches(
                segments,
                revision.operation,
                target_language=target_language_name,
                custom_instruction=revision.custom_instruction,
                should_cancel=revision_cancelled,
                source_language=revision.source_language,
            ):
                cache_hits_total += batch.cache_hits
                cache_misses_total += batch.cache_misses
                tokens_saved_total += batch.estimated_tokens_saved
                # The LAST model to answer wins the record. A pass that
                # started on flash-lite and finished on a fallback is a pass
                # whose quota story is the fallback's, and that is the one
                # worth being able to read back.
                if getattr(batch, 'model_name', ''):
                    model_used = batch.model_name

                for result in batch.segments:
                    source = result.source
                    revision_segment = AIRevisionSegment.objects.create(
                        revision=revision,
                        original_segment=source,
                        speaker_label=source.speaker_label,
                        start_time=source.start_time,
                        end_time=source.end_time,
                        original_text=result.original_text,
                        processed_text=result.processed_text,
                        changed=result.changed,
                    )
                    processed_count += 1

                    broadcast_update(job_id, {
                        'type': 'ai_revision_segment_added',
                        'revision_id': str(revision.id),
                        'processed_count': processed_count,
                        'total_segments': len(segments),
                        'segment': {
                            'id': str(revision_segment.id),
                            'speaker_label': revision_segment.speaker_label,
                            'start_time': revision_segment.start_time,
                            'end_time': revision_segment.end_time,
                            'original_text': revision_segment.original_text,
                            'processed_text': revision_segment.processed_text,
                            'changed': revision_segment.changed,
                            'accepted': revision_segment.accepted,
                        },
                    })

                usage_total = usage_total + batch.usage
                degraded = degraded or batch.degraded
                if batch.usage.total_tokens:
                    ai_budget.record_usage(revision, batch.usage, job=revision.job)

        # ------------------------------------------------------------------
        # Quality gate.
        #
        # Runs only when a profile was requested (see
        # TranscriptionJobViewSet.ai_revisions), so a revision dispatched the
        # way every existing caller dispatches one reaches the code below
        # having done exactly what it did before this feature existed.
        #
        # Placed AFTER the segment rows are written and BEFORE the run is
        # marked completed, which is the only position that satisfies both
        # halves of the requirement: the reviewer needs the whole candidate
        # to judge terminology consistency and completeness across the
        # range, and nothing may reach a "finished" state on the strength of
        # a model having returned text (RULE 12).
        # ------------------------------------------------------------------
        quality_result = None
        if revision.quality_profile and not revision_cancelled():
            quality_result = _run_quality_gate(
                revision, processor, job_id, revision_cancelled,
            )
            if quality_result is not None and quality_result.usage.total_tokens:
                usage_total = usage_total + quality_result.usage
                ai_budget.record_usage(
                    revision, quality_result.usage, job=revision.job,
                    # The gate makes several calls but writes one ledger row,
                    # so the count has to travel with it -- the provider's
                    # quota is metered in requests, not tokens.
                    request_count=quality_result.request_count or 1,
                )

        # iter_batches stops yielding the moment cancellation is observed, so
        # falling out of the loop early looks identical to finishing. Ask
        # again rather than reporting a truncated run as complete.
        if revision_cancelled():
            revision.prompt_tokens = usage_total.prompt_tokens
            revision.completion_tokens = usage_total.completion_tokens
            revision.total_tokens = usage_total.total_tokens
            revision.cache_hits = cache_hits_total
            revision.cache_misses = cache_misses_total
            revision.estimated_tokens_saved = tokens_saved_total
            if model_used:
                revision.model_name = model_used
            revision.save()
            mark_revision_cancelled()
            return

        revision.status = 'completed'
        revision.degraded = degraded
        revision.prompt_tokens = usage_total.prompt_tokens
        revision.completion_tokens = usage_total.completion_tokens
        revision.total_tokens = usage_total.total_tokens
        revision.cache_hits = cache_hits_total
        revision.cache_misses = cache_misses_total
        revision.estimated_tokens_saved = tokens_saved_total
        # WHICH MODEL ANSWERED, so "are we still on flash-lite or have we
        # been running on a fallback for a week?" is a question the data can
        # answer. It was computed for the cache key and thrown away.
        if model_used:
            revision.model_name = model_used
        revision.completed_at = timezone.now()
        revision.save()

        changed_count = revision.segments.filter(changed=True).count()
        broadcast_update(job_id, {
            'type': 'ai_revision_status_update',
            'revision_id': str(revision.id),
            'status': 'completed',
            'total_segments': processed_count,
            'changed_segments': changed_count,
            'degraded': degraded,
            'total_tokens': usage_total.total_tokens,
            'cache_hits': cache_hits_total,
            'cache_misses': cache_misses_total,
            'estimated_tokens_saved': tokens_saved_total,
            # Always present, 'not_run' when no profile was requested, so the
            # client never has to distinguish "this build has no quality
            # pipeline" from "this run skipped it".
            'quality_status': revision.quality_status,
            'quality_profile': revision.quality_profile,
            'quality_reason': revision.quality_reason,
            'qa_iterations': revision.qa_iterations,
            'quality_confidence': revision.quality_confidence,
            'detected_domain': revision.detected_domain,
            'detected_register': revision.detected_register,
            'critical_issue_count': revision.critical_issue_count,
            'high_issue_count': revision.high_issue_count,
            'medium_issue_count': revision.medium_issue_count,
            'low_issue_count': revision.low_issue_count,
            'submission_ready': revision.submission_ready,
        })
        logger.info(
            f"AIRevision {revision.id} completed: {processed_count} segment(s), "
            f"{changed_count} changed, {usage_total.total_tokens} tokens, "
            f"cache_hits={cache_hits_total}, cache_misses={cache_misses_total}, "
            f"degraded={degraded}, quality={revision.quality_status}."
        )

    except AIAssistError as e:
        # .detail may quote the provider's response body -- log only.
        logger.error(f"AI revision {revision_id} failed: {e.detail}", exc_info=True)
        fail(e.safe_message)

    except Exception as e:
        logger.error(
            f"Unexpected error in AI revision pipeline for AIRevision {revision_id}: {str(e)}",
            exc_info=True,
        )
        fail('AI processing failed unexpectedly. Please try again.')


def _quality_language_code(revision):
    """
    The ISO code the deterministic typography rules key on.

    For a translation that is the target language; for a same-language pass
    it is the source, because the text being checked is in the source
    language. Getting this backwards would apply French spacing rules to
    English output and report a finding on every colon.
    """
    if revision.operation == 'translate' and revision.target_language:
        return revision.target_language
    return revision.source_language or None


def _run_quality_gate(revision, processor, job_id, should_cancel):
    """
    Runs the requested quality profile over this revision's segments and
    persists the outcome. Returns the QualityResult, or None when no engine
    could be built.

    NEVER RAISES. A quality pass is a safety net, and a safety net that can
    destroy the work it was protecting is worse than none: the segments are
    already written and correct at this point, so any unexpected failure
    here is recorded as a technical failure on the quality axis and the
    revision still completes. The one thing it must never do is leave
    quality_status at 'validated' when it did not validate anything.

    The provider is taken from the processor that just ran rather than built
    fresh. Two reasons: one authenticated client per run rather than two
    (spec 23), and it is what makes the whole gate testable with the
    FakeStructuredProvider the existing suite already injects.
    """
    try:
        from core.services.ai_assist.quality import QualityStatus, build_engine
    except ModuleNotFoundError:
        from backend.core.services.ai_assist.quality import QualityStatus, build_engine

    from .models import AIQualityReport

    rows = list(revision.segments.all().order_by('start_time'))
    if not rows:
        return None

    engine = build_engine(
        revision.quality_profile, provider=getattr(processor, 'provider', None)
    )
    if engine is None:
        logger.warning(
            '[AI QUALITY] Unknown profile %r on revision %s; skipping the gate.',
            revision.quality_profile, revision.id,
        )
        # A profile was asked for and could not be built, so this run is NOT
        # 'not_run' -- that would claim no gate was requested. It is a gate
        # that could not execute, which is a technical failure.
        revision.quality_status = QualityStatus.TECHNICAL_FAILURE.value
        revision.quality_reason = (
            'The requested quality profile is not available on this server.'
        )
        revision.save(update_fields=['quality_status', 'quality_reason'])
        return None

    revision.quality_status = 'auditing'
    revision.save(update_fields=['quality_status'])

    def on_stage(stage, payload):
        broadcast_update(job_id, {
            'type': 'ai_revision_quality_update',
            'revision_id': str(revision.id),
            'stage': stage,
            **payload,
        })

    pairs = [
        {'source': row.original_text or '', 'target': row.effective_text or ''}
        for row in rows
    ]

    try:
        result = engine.run(
            pairs,
            target_language=_ai_language_name(revision.target_language),
            target_language_code=_quality_language_code(revision),
            source_language=revision.source_language,
            should_cancel=should_cancel,
            on_stage=on_stage,
        )
    except Exception as exc:
        logger.error(
            '[AI QUALITY] Gate failed unexpectedly on revision %s: %s',
            revision.id, exc, exc_info=True,
        )
        revision.quality_status = QualityStatus.TECHNICAL_FAILURE.value
        revision.quality_reason = (
            'The quality pipeline could not complete. The AI result above '
            'has not been checked.'
        )
        revision.save(update_fields=['quality_status', 'quality_reason'])
        return None

    # Persist repaired text. Only rows whose text actually moved are written,
    # so an unchanged run costs no UPDATEs -- and `changed` is recomputed
    # from the strings rather than assumed, exactly as the primary pass does,
    # so a repair that restores a segment to its original text correctly
    # stops being a diff entry.
    for index, row in enumerate(rows):
        if index >= len(result.texts):
            break
        repaired = result.texts[index]
        if repaired == row.processed_text:
            continue
        row.processed_text = repaired
        row.changed = repaired.strip() != (row.original_text or '').strip()
        row.save(update_fields=['processed_text', 'changed'])
        broadcast_update(job_id, {
            'type': 'ai_revision_segment_updated',
            'revision_id': str(revision.id),
            'segment': {
                'id': str(row.id),
                'processed_text': row.processed_text,
                'changed': row.changed,
                'accepted': row.accepted,
            },
        })

    counts = result.counts()
    revision.quality_status = result.status.value
    revision.quality_reason = result.reason
    revision.qa_iterations = result.iterations
    revision.quality_confidence = result.confidence
    revision.critical_issue_count = counts.get('critical', 0)
    revision.high_issue_count = counts.get('high', 0)
    revision.medium_issue_count = counts.get('medium', 0)
    revision.low_issue_count = counts.get('low', 0)
    revision.quality_prompt_version = result.prompt_version
    if result.domain_profile is not None:
        revision.detected_domain = (result.domain_profile.domain or '')[:80]
        revision.detected_register = (result.domain_profile.register or '')[:80]
    revision.save(update_fields=[
        'quality_status', 'quality_reason', 'qa_iterations',
        'quality_confidence', 'critical_issue_count', 'high_issue_count',
        'medium_issue_count', 'low_issue_count', 'quality_prompt_version',
        'detected_domain', 'detected_register',
    ])

    try:
        AIQualityReport.objects.create(
            revision=revision,
            profile=result.profile_name,
            status=result.status.value,
            reason=result.reason,
            iterations=result.iterations,
            confidence=result.confidence,
            model_name=result.model_name,
            prompt_version=result.prompt_version,
            total_tokens=result.usage.total_tokens,
            total_latency_seconds=sum(result.stage_latencies.values()),
            report=result.to_report(),
        )
    except Exception as exc:
        # Same policy as ai_budget.record_usage: the audit record is
        # valuable, but losing it must not fail a revision whose text and
        # whose verdict are already correctly stored on the revision itself.
        logger.error(
            '[AI QUALITY] Failed to store the quality report for %s: %s',
            revision.id, exc, exc_info=True,
        )

    broadcast_update(job_id, {
        'type': 'ai_revision_quality_update',
        'revision_id': str(revision.id),
        'stage': 'validation',
        'quality_status': result.status.value,
        'quality_reason': result.reason,
        'counts': counts,
        'iterations': result.iterations,
        'confidence': result.confidence,
        'domain': revision.detected_domain,
        'register': revision.detected_register,
        # The issue list itself is deliberately NOT broadcast. It can be
        # large, the socket is a progress channel, and the review UI fetches
        # the stored report when the user opens it.
        'issue_count': len(result.issues),
    })
    return result
