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

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
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
