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
    ]

    SOURCE_CHOICES = [
        ('upload', 'File Upload'),
        ('url', 'Public Media URL'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='jobs')
    input_source = models.CharField(max_length=50, choices=SOURCE_CHOICES)
    source_url = models.URLField(blank=True, null=True, max_length=1000)
    file_path = models.CharField(max_length=1000, blank=True, null=True)
    # Optional request headers (JSON) to use when downloading protected resources
    headers = models.JSONField(blank=True, null=True)
    # Optional raw cookie string to send with requests (e.g. 'session=...; other=...')
    cookies = models.TextField(blank=True, null=True)
    # Optional path to a cookies file uploaded to the server (for yt-dlp --cookies)
    cookies_file = models.CharField(max_length=1000, blank=True, null=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='queued')
    detected_language = models.CharField(max_length=10, blank=True, null=True)
    language_confidence = models.FloatField(blank=True, null=True)
    language = models.CharField(max_length=10, blank=True, null=True)
    enable_code_switching = models.BooleanField(default=False)
    # Final cleaned/polished transcript produced by post-processing/editor
    final_transcript = models.TextField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.id} - {self.status}"

class TranscriptSegment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(TranscriptionJob, on_delete=models.CASCADE, related_name='segments')
    speaker_label = models.CharField(max_length=100, default='Unknown')
    start_time = models.FloatField()  # seconds
    end_time = models.FloatField()    # seconds
    text = models.TextField()
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
