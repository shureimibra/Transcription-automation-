from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APITransactionTestCase
from .models import Project, TranscriptionJob, TranscriptSegment
import json

class TranscriptionModelsTestCase(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="Test Project",
            description="A test description"
        )
        self.job = TranscriptionJob.objects.create(
            project=self.project,
            input_source="url",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )

    def test_project_creation(self):
        self.assertEqual(self.project.name, "Test Project")
        self.assertEqual(str(self.project), "Test Project")

    def test_job_creation(self):
        self.assertEqual(self.job.status, "queued")
        self.assertEqual(str(self.job), f"{self.job.id} - queued")

    def test_segment_creation(self):
        segment = TranscriptSegment.objects.create(
            job=self.job,
            speaker_label="Speaker 0",
            start_time=0.0,
            end_time=2.5,
            text="Hello world"
        )
        self.assertEqual(segment.text, "Hello world")
        self.assertEqual(str(segment), "[0.00-2.50] Speaker 0: Hello world")

class TranscriptionAPITestCase(APITransactionTestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="API Test Project",
            description="API test desc"
        )
        self.job = TranscriptionJob.objects.create(
            project=self.project,
            input_source="url",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        self.segment = TranscriptSegment.objects.create(
            job=self.job,
            speaker_label="Speaker 0",
            start_time=1.0,
            end_time=3.5,
            text="Mock segment text"
        )

    def test_list_projects(self):
        url = reverse('project-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_create_project(self):
        url = reverse('project-list')
        data = {'name': 'New Project', 'description': 'Description'}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Project.objects.count(), 2)

    def test_create_job(self):
        url = reverse('transcription-list')
        data = {
            'project': self.project.id,
            'input_source': 'url',
            'source_url': 'https://www.youtube.com/watch?v=abc'
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # Wait a brief moment for the background thread to advance status
        import time
        time.sleep(0.5)
        # Verify the celery or background thread was triggered (the status would advance from 'queued')
        job = TranscriptionJob.objects.get(id=response.data['id'])
        self.assertIn(job.status, ['downloading', 'transcribing', 'completed', 'failed'])

    def test_get_segments(self):
        url = reverse('transcription-segments', kwargs={'pk': self.job.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['text'], "Mock segment text")

    def test_export_txt(self):
        url = reverse('export-transcript', kwargs={'job_id': self.job.id, 'format_type': 'txt'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'text/plain')
        self.assertIn("Speaker 0: Mock segment text", response.content.decode('utf-8'))

    def test_export_docx(self):
        url = reverse('export-transcript', kwargs={'job_id': self.job.id, 'format_type': 'docx'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
