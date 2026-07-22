from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APITransactionTestCase
from .models import Project, TranscriptionJob, TranscriptSegment
from core.services.ootranscript_editor import clean_transcript
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

    def test_create_job_with_language_override(self):
        url = reverse('transcription-list')
        data = {
            'project': self.project.id,
            'input_source': 'url',
            'source_url': 'https://example.com/audio.mp3',
            'language': 'oromo'
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['language'], 'oromo')
        job = TranscriptionJob.objects.get(id=response.data['id'])
        self.assertEqual(job.language, 'oromo')

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

    def test_ootranscript_clean_text(self):
        raw = "eeyyee ani tiyyara fi sigalee miti dubbii."
        expected = "Ani xiyyaara fi dubbii."
        cleaned = clean_transcript({'text': raw}, verbatim=False)
        self.assertIn("Ani", cleaned)
        self.assertNotIn("eeyyee", cleaned)
        self.assertIn("xiyyaara", cleaned)

    def test_ootranscript_endpoint(self):
        url = reverse('ootranscript')
        payload = {'text': 'eeyyee ani tiyyaara tiyyara.'}
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('xiyyaara', response.content.decode('utf-8'))

    def test_arabic_script_to_qubee_transliteration(self):
        raw = "افانا رو مو بیک تا؟"
        cleaned = clean_transcript({'text': raw}, verbatim=True)
        self.assertIn("Afaan", cleaned)
        self.assertIn("Oromoo", cleaned)
        self.assertIn("beektaa", cleaned)
        # Ensure no Arabic script characters remain
        import re
        self.assertIsNone(re.search(r'[\u0600-\u06FF]', cleaned))

    def test_language_registry_view(self):
        url = reverse('language-registry')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        codes = [l['code'] for l in response.data['languages']]
        self.assertIn('om', codes)
        self.assertIn('ki', codes)
        self.assertIn('so', codes)
        self.assertIn('ru', codes)
        self.assertIn('ar', codes)

    def test_3layer_kikuyu_orthography_validation(self):
        from .languages import sanitize_and_validate_transcript
        # Kikuyu: 'f', 'z', 'x' are forbidden letters
        raw = "Nĩ wega mũno fox."
        clean_text, hallucinated, logs, corrections = sanitize_and_validate_transcript(raw, "ki")
        self.assertTrue(hallucinated)
        self.assertNotIn("fox", clean_text)
        self.assertTrue(len(logs) > 0)
        self.assertTrue(len(corrections) > 0)

    def test_3layer_oromo_qubee_validation(self):
        from .languages import sanitize_and_validate_transcript
        # Oromo: valid doubled vowels and digraphs pass cleanly
        raw = "Afaan Oromoo beektaa maatiin koo nagaa jiru."
        clean_text, hallucinated, logs, corrections = sanitize_and_validate_transcript(raw, "om")
        self.assertFalse(hallucinated)
        self.assertEqual(clean_text, raw)

    def test_3layer_cyrillic_russian_validation(self):
        from .languages import sanitize_and_validate_transcript
        # Russian Cyrillic: Latin letters inside Cyrillic text must trigger script bleed violation
        raw = "Привет world."
        clean_text, hallucinated, logs, corrections = sanitize_and_validate_transcript(raw, "ru")
        self.assertTrue(hallucinated)
        self.assertNotIn("world", clean_text)
        self.assertIn("Привет", clean_text)

    def test_linguistic_validation_endpoint(self):
        url = reverse('validate-transcription')
        payload = {
            'text': 'Nĩ wega fox',
            'language': 'ki'
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertTrue(response.data['hallucinated'])
        self.assertEqual(response.data['language_code'], 'ki')
        self.assertTrue(len(response.data['logs']) > 0)
        self.assertTrue(len(response.data['corrections']) > 0)

    def test_3layer_somali_code_switching_bypass(self):
        from .languages import sanitize_and_validate_transcript
        # Somali: 'p', 'v', 'z' are forbidden characters in native words
        raw = "Waa hagaag kani waa video iyo process."
        
        # 1. Without code-switching enabled: forbidden characters are stripped
        clean_text_no_cs, hallucinated_no_cs, _, _ = sanitize_and_validate_transcript(raw, "so", enable_code_switching=False)
        self.assertTrue(hallucinated_no_cs)
        self.assertNotIn("video", clean_text_no_cs)
        self.assertNotIn("process", clean_text_no_cs)
        
        # 2. With code-switching enabled: valid English words bypass orthography restrictions
        clean_text_cs, hallucinated_cs, _, _ = sanitize_and_validate_transcript(raw, "so", enable_code_switching=True)
        # It shouldn't trigger orthography violations for video and process
        self.assertIn("video", clean_text_cs)
        self.assertIn("process", clean_text_cs)

    def test_local_file_path_resolution(self):
        from core.services.ai_pipeline import AudioExtractor
        
        # Test file:// URI decoding
        uri = "file:///C:/Users/Administrator/AppData/Local/Temp/Gari.m4a"
        resolved = AudioExtractor._resolve_local_file_path(uri)
        # Slashes will be normalized to backslashes
        self.assertEqual(resolved, "C:\\Users\\Administrator\\AppData\\Local\\Temp\\Gari.m4a")

        # Test nested https://file:/// URI decoding
        nested_uri = "https://file:///C:/Users/Administrator/AppData/Local/Temp/Gari.m4a"
        resolved_nested = AudioExtractor._resolve_local_file_path(nested_uri)
        self.assertEqual(resolved_nested, "C:\\Users\\Administrator\\AppData\\Local\\Temp\\Gari.m4a")

        # Test URL-encoded characters (e.g. space)
        encoded_uri = "file:///C:/Users/Administrator/My%20Documents/Gari.m4a"
        resolved_encoded = AudioExtractor._resolve_local_file_path(encoded_uri)
        self.assertEqual(resolved_encoded, "C:\\Users\\Administrator\\My Documents\\Gari.m4a")

    def test_is_prompt_echo_filter(self):
        from core.services.ai_pipeline import is_prompt_echo
        
        prompt = "This is a high-fidelity transcript in native English that naturally mixes in English loanwords."
        
        # 1. Exact match / substring match
        self.assertTrue(is_prompt_echo("This is a high-fidelity transcript in native English", prompt))
        
        # 2. Key phrase overlap (4 or more consecutive words)
        self.assertTrue(is_prompt_echo("This is a high-fidelity transcript in native English, technical terms.", prompt))
        
        # 3. Non-overlapping text
        self.assertFalse(is_prompt_echo("Hello my friend, how are you today?", prompt))




