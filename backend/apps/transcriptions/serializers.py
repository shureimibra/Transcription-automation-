from rest_framework import serializers
from .models import Project, TranscriptionJob, TranscriptSegment

class TranscriptSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TranscriptSegment
        fields = ['id', 'speaker_label', 'start_time', 'end_time', 'text', 'confidence', 'words']

class TranscriptionJobSerializer(serializers.ModelSerializer):
    segments = TranscriptSegmentSerializer(many=True, read_only=True)
    source_url = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    
    class Meta:
        model = TranscriptionJob
        fields = [
            'id', 'project', 'input_source', 'source_url', 
            'file_path', 'status', 'detected_language', 
            'language_confidence', 'language', 'enable_code_switching', 'final_transcript', 'error_message', 'created_at', 
            'completed_at', 'segments', 'headers', 'cookies', 'cookies_file'
        ]
        read_only_fields = ['id', 'status', 'detected_language', 'language_confidence', 'final_transcript', 'error_message', 'created_at', 'completed_at']

class ProjectSerializer(serializers.ModelSerializer):
    jobs = TranscriptionJobSerializer(many=True, read_only=True)

    class Meta:
        model = Project
        fields = ['id', 'name', 'description', 'created_at', 'updated_at', 'jobs']


class LinguisticValidationRequestSerializer(serializers.Serializer):
    text = serializers.CharField(required=True, allow_blank=True)
    language = serializers.CharField(required=True, max_length=50)

