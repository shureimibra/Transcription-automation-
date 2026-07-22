from rest_framework import viewsets, status, views
from rest_framework.response import Response
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
import io
import os
import tempfile

from .models import Project, TranscriptionJob, TranscriptSegment
from .serializers import ProjectSerializer, TranscriptionJobSerializer, TranscriptSegmentSerializer

# Helper import to trigger celery task in production
from .tasks import run_transcription_pipeline
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from django.http import HttpResponse
try:
    from backend.core.services.ootranscript_editor import clean_transcript
except ModuleNotFoundError:
    from core.services.ootranscript_editor import clean_transcript

class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all().order_by('-created_at')
    serializer_class = ProjectSerializer

class TranscriptionJobViewSet(viewsets.ModelViewSet):
    queryset = TranscriptionJob.objects.all().order_by('-created_at')
    serializer_class = TranscriptionJobSerializer

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
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
        else:
            # Clean and format source_url if provided
            source_url = data.get('source_url', '')
            if source_url and isinstance(source_url, str):
                source_url = source_url.strip()
                if source_url and not (source_url.startswith('http://') or source_url.startswith('https://')):
                    source_url = 'https://' + source_url
                data['source_url'] = source_url

        # Robust project resolution
        project_id = data.get('project')
        valid_proj = None
        if project_id and str(project_id).strip() not in ('null', 'undefined', ''):
            try:
                valid_proj = Project.objects.filter(id=project_id).first()
            except Exception:
                valid_proj = None

        if not valid_proj:
            default_proj, _ = Project.objects.get_or_create(
                name='Default Project',
                defaults={'description': 'Default workspace'}
            )
            data['project'] = default_proj.id

        # Handle headers JSON parsing
        if 'headers' in data and isinstance(data['headers'], str):
            try:
                import json
                parsed = json.loads(data['headers'])
                data['headers'] = parsed
            except Exception:
                data['headers'] = None

        if 'language' in data and isinstance(data['language'], str):
            data['language'] = data['language'].strip().lower()

        # Handle cookies_file: remove uploaded file object from data dict if present to avoid DRF CharField validation error
        if 'cookies_file' in data and not isinstance(data['cookies_file'], str):
            data.pop('cookies_file', None)

        cookies_file = request.FILES.get('cookies_file')
        if cookies_file:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.cookies', prefix='cookies_', dir=None)
            for chunk in cookies_file.chunks():
                tmp.write(chunk)
            tmp.close()
            data['cookies_file'] = tmp.name

        serializer = self.get_serializer(data=data)
        if not serializer.is_valid():
            print(f"[JOB CREATION VALIDATION ERROR]: {serializer.errors}")
            return Response({"error": "Invalid job request data", "details": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        if temp_file_path:
            job = serializer.save(file_path=temp_file_path)
        else:
            job = serializer.save()

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
            thread = threading.Thread(target=target_fn, args=(job.id,))
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


class ExportTranscriptView(views.APIView):
    """
    Server-side document generator implementing export strategies.
    Generates and returns DOCX, TXT, or PDF exports.
    """
    def get(self, request, job_id, format_type):
        job = get_object_or_404(TranscriptionJob, id=job_id)
        segments = job.segments.all().order_by('start_time')

        if format_type == 'docx':
            try:
                from docx import Document
                doc = Document()
                doc.add_heading(f"Transcript Report - Job {job.id}", level=1)
                
                for seg in segments:
                    time_str = f"[{int(seg.start_time // 60):02d}:{int(seg.start_time % 60):02d}]"
                    p = doc.add_paragraph()
                    r1 = p.add_run(f"{time_str} {seg.speaker_label}: ")
                    r1.bold = True
                    p.add_run(seg.text)

                buffer = io.BytesIO()
                doc.save(buffer)
                buffer.seek(0)
                
                response = HttpResponse(buffer.read(), content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
                response['Content-Disposition'] = f'attachment; filename="transcript_{job.id}.docx"'
                return response
            except ImportError:
                return Response({"error": "python-docx package is not configured on server."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        elif format_type == 'txt':
            lines = []
            for seg in segments:
                time_str = f"[{int(seg.start_time // 60):02d}:{int(seg.start_time % 60):02d}]"
                lines.append(f"{time_str} {seg.speaker_label}: {seg.text}\n")
            
            response = HttpResponse("".join(lines), content_type='text/plain')
            response['Content-Disposition'] = f'attachment; filename="transcript_{job.id}.txt"'
            return response

        else:
            return Response({"error": "Unsupported export format requested."}, status=status.HTTP_400_BAD_REQUEST)


class LanguageRegistryView(APIView):
    """Returns the combined list of default recognized languages and trained native languages."""
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        try:
            from .languages import LANGUAGE_PROFILES
            languages_list = [
                {"code": p.code, "name": p.name}
                for p in LANGUAGE_PROFILES.values()
            ]
            languages_list = sorted(languages_list, key=lambda x: x["name"])
            return Response({"languages": languages_list})
        except Exception:
            default_languages = [
                {"code": "ki", "name": "Kikuyu (Gĩkũyũ)"},
                {"code": "om", "name": "Oromo (Afaan Oromoo)"},
                {"code": "so", "name": "Somali (Soomaali)"},
                {"code": "ru", "name": "Russian (Русский)"},
                {"code": "ar", "name": "Arabic (العربية)"},
                {"code": "zh", "name": "Chinese (中文)"},
                {"code": "de", "name": "German (Deutsch)"},
                {"code": "fr", "name": "French (Français)"},
                {"code": "tr", "name": "Turkish (Türkçe)"},
                {"code": "es", "name": "Spanish (Español)"},
                {"code": "am", "name": "Amharic (አማርኛ)"},
                {"code": "en", "name": "English"},
            ]
            return Response({"languages": default_languages})


class LinguisticValidationView(APIView):
    """
    3-Layer Linguistic Validation Endpoint
    POST payload: {"text": "raw transcript", "language": "ki"}
    Returns JSON response:
    {
        "success": true,
        "raw_text": "raw transcript",
        "clean_text": "sanitized text",
        "hallucinated": true/false,
        "logs": [...],
        "corrections": [...]
    }
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        try:
            from .serializers import LinguisticValidationRequestSerializer
            from .languages import sanitize_and_validate_transcript, get_language_profile

            serializer = LinguisticValidationRequestSerializer(data=request.data)
            if not serializer.is_valid():
                return Response({
                    "success": False,
                    "error": "Invalid validation payload",
                    "details": serializer.errors
                }, status=status.HTTP_400_BAD_REQUEST)

            raw_text = serializer.validated_data['text']
            lang_code = serializer.validated_data['language']

            clean_text, hallucinated, logs, corrections = sanitize_and_validate_transcript(raw_text, lang_code)
            profile = get_language_profile(lang_code)

            return Response({
                "success": True,
                "language_code": profile.code,
                "language_name": profile.name,
                "raw_text": raw_text,
                "clean_text": clean_text,
                "hallucinated": hallucinated,
                "logs": logs,
                "corrections": corrections
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({
                "success": False,
                "error": f"Linguistic validation pipeline error: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class OotranscriptView(APIView):
    """Accepts raw ASR output as JSON and returns the cleaned Oromo transcript (plain text).

    POST payload can be {"text": "raw transcript"} or {"segments": [{"speaker":..., "text":...}, ...]}
    Returns plain text response containing only the polished transcript.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        data = request.data
        verbatim = bool(request.query_params.get('verbatim', False))
        try:
            cleaned = clean_transcript(data, verbatim=verbatim)
            return HttpResponse(cleaned, content_type='text/plain; charset=utf-8')
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

