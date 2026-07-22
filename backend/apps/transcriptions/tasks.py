import os
import time
import logging
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

from .models import TranscriptionJob, TranscriptSegment
try:
    from backend.core.services.ai_pipeline import AudioExtractor, WhisperTranscriber
    from backend.core.services.ootranscript_editor import clean_transcript
except ModuleNotFoundError:
    from core.services.ai_pipeline import AudioExtractor, WhisperTranscriber
    from core.services.ootranscript_editor import clean_transcript

logger = logging.getLogger(__name__)

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

@shared_task
def run_transcription_pipeline(job_id):
    logger.info(f"Starting transcription pipeline for Job: {job_id}")
    try:
        job = TranscriptionJob.objects.get(id=job_id)
    except TranscriptionJob.DoesNotExist:
        logger.error(f"Job {job_id} does not exist.")
        return

    temp_wav_path = os.path.abspath(f"job_{job_id}_temp.wav")
    
    try:
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
        if job.input_source == 'url':
            # Pass optional auth headers and cookies from the job to the extractor
            headers = job.headers if hasattr(job, 'headers') else None
            cookies = job.cookies if hasattr(job, 'cookies') else None
            cookies_file = getattr(job, 'cookies_file', None)
            success = AudioExtractor.download_and_extract_url(
                job.source_url,
                temp_wav_path,
                headers=headers,
                cookies=cookies,
                cookies_file_path=cookies_file
            )
        elif job.input_source == 'upload':
            if job.file_path and os.path.exists(job.file_path):
                success = AudioExtractor.extract_from_file(job.file_path, temp_wav_path)
            else:
                logger.error(f"Upload file path missing or not found for job {job.id}: {job.file_path}")
                success = False

        if not success:
            raise RuntimeError("Audio extraction or download failed.")

        # 3. Update status to Transcribing
        job.status = 'transcribing'
        job.save()
        broadcast_update(job_id, {
            'type': 'status_update',
            'status': 'transcribing',
            'message': 'Transcribing speech to text...'
        })

        # 4. Transcribe
        transcriber = WhisperTranscriber(model_size="base")
        
        segment_count = 0
        for chunk in transcriber.transcribe(
            temp_wav_path,
            enable_vad=True,
            language=job.language if getattr(job, 'language', None) else None,
            enable_code_switching=getattr(job, 'enable_code_switching', False)
        ):
            # Assign alternating speaker label for demo
            sec = int(chunk['start'])
            speaker = f"Speaker {(sec // 15) % 2}"

            # Save segment to database
            segment = TranscriptSegment.objects.create(
                job=job,
                speaker_label=speaker,
                start_time=chunk['start'],
                end_time=chunk['end'],
                text=chunk['text'],
                confidence=chunk['confidence']
            )

            # Broadcast the segment to WebSocket
            broadcast_update(job_id, {
                'type': 'segment_added',
                'segment': {
                    'id': str(segment.id),
                    'speaker_label': speaker,
                    'start_time': chunk['start'],
                    'end_time': chunk['end'],
                    'text': chunk['text'],
                    'confidence': chunk['confidence']
                }
            })
            segment_count += 1
            # Add short delay to simulate streaming feel
            time.sleep(1.0)

        # Persist any language detection metadata from the transcription step
        if getattr(transcriber, 'last_info', None) is not None:
            job.detected_language = getattr(transcriber.last_info, 'language', None)
            job.language_confidence = getattr(transcriber.last_info, 'language_probability', None)

        # Persist any language detection metadata from the transcription step
        if getattr(transcriber, 'last_info', None) is not None:
            job.detected_language = getattr(transcriber.last_info, 'language', None)
            job.language_confidence = getattr(transcriber.last_info, 'language_probability', None)

        # 5. Run post-processing editor (Oromo GoTranscript rules) and save final transcript
        try:
            segs = list(job.segments.all().values('speaker_label', 'start_time', 'text'))
            # adapt to editor expected keys
            segs_for_editor = []
            for s in segs:
                segs_for_editor.append({
                    'speaker_label': s.get('speaker_label'),
                    'start_time': s.get('start_time'),
                    'text': s.get('text')
                })
            cleaned = clean_transcript({'segments': segs_for_editor}, verbatim=False)
            job.final_transcript = cleaned
            job.save()
            broadcast_update(job_id, {
                'type': 'final_transcript',
                'transcript': cleaned
            })
        except Exception as e:
            logger.warning(f"Post-processing editor failed for Job {job_id}: {e}")

        # 6. Complete Job
        job.status = 'completed'
        job.completed_at = timezone.now()
        job.save()

        broadcast_update(job_id, {
            'type': 'status_update',
            'status': 'completed',
            'message': 'Transcription successfully completed.'
        })

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
        # Remove any uploaded cookies file left on disk and clear job reference
        try:
            if hasattr(job, 'cookies_file') and job.cookies_file:
                if os.path.exists(job.cookies_file):
                    try:
                        os.remove(job.cookies_file)
                    except Exception:
                        pass
                job.cookies_file = None
                job.save()
        except Exception:
            pass
