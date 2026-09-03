"""
The authorization boundary on the transcription API and its WebSocket.

THE HOLE THIS CLOSES WAS MEASURED, NOT THEORETICAL. Before this, an anonymous
`GET /api/v1/transcriptions/` returned 224 jobs with full segment text,
speaker labels and Eagle source URLs, and an anonymous browser context
connected successfully to a live job's WebSocket. Both were verified against
the running server during the Phase 4 research pass.

These tests fail loudly if either ever reopens. They live in their own module
rather than in tests.py because that file is 11,000 lines and these assertions
are about one thing.
"""
from django.contrib.auth.models import AnonymousUser, User
from django.test import TransactionTestCase
from rest_framework.test import APITestCase

from .models import Project, TranscriptionJob, TranscriptSegment

PASSWORD = 'an-adequately-long-passphrase'


class TranscriptionAPIAuthorizationTestCase(APITestCase):

    def setUp(self):
        self.project = Project.objects.create(name='Auth project')
        self.alice = User.objects.create_user(username='alice', password=PASSWORD)
        self.bob = User.objects.create_user(username='bob', password=PASSWORD)
        self.admin = User.objects.create_user(
            username='ops', password=PASSWORD, is_staff=True,
        )
        self.alice_job = TranscriptionJob.objects.create(
            project=self.project, input_source='url',
            source_url='https://example.test/alice.mp3', owner=self.alice,
        )
        self.orphan_job = TranscriptionJob.objects.create(
            project=self.project, input_source='url',
            source_url='https://example.test/legacy.mp3', owner=None,
        )

    @staticmethod
    def _ids(response):
        rows = response.data if isinstance(response.data, list) else response.data['results']
        return {str(r['id']) for r in rows}

    # -- anonymous ------------------------------------------------------ #

    def test_anonymous_cannot_list_transcriptions(self):
        self.assertIn(self.client.get('/api/v1/transcriptions/').status_code, (401, 403))

    def test_anonymous_cannot_read_one_transcription(self):
        response = self.client.get(f'/api/v1/transcriptions/{self.alice_job.id}/')
        self.assertIn(response.status_code, (401, 403))

    def test_anonymous_cannot_export(self):
        response = self.client.get(f'/api/v1/transcriptions/{self.alice_job.id}/export/txt/')
        self.assertIn(response.status_code, (401, 403))

    def test_anonymous_cannot_read_the_reference_endpoints(self):
        for path in ('/api/v1/languages/', '/api/v1/ai-capabilities/', '/api/v1/projects/'):
            with self.subTest(path=path):
                self.assertIn(self.client.get(path).status_code, (401, 403), path)

    # -- cross-user ----------------------------------------------------- #

    def test_a_user_sees_only_their_own_jobs(self):
        self.client.force_authenticate(self.alice)

        response = self.client.get('/api/v1/transcriptions/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._ids(response), {str(self.alice_job.id)})

    def test_a_user_cannot_read_another_users_job(self):
        self.client.force_authenticate(self.bob)

        response = self.client.get(f'/api/v1/transcriptions/{self.alice_job.id}/')

        # 404 rather than 403: whether a job exists is itself information.
        self.assertEqual(response.status_code, 404)

    def test_a_user_cannot_export_another_users_job(self):
        self.client.force_authenticate(self.bob)

        response = self.client.get(f'/api/v1/transcriptions/{self.alice_job.id}/export/txt/')

        self.assertEqual(response.status_code, 404)

    def test_a_user_cannot_edit_a_segment_of_another_users_job(self):
        segment = TranscriptSegment.objects.create(
            job=self.alice_job, start_time=0, end_time=1, text='mine',
        )
        self.client.force_authenticate(self.bob)

        response = self.client.patch(
            f'/api/v1/transcriptions/{self.alice_job.id}/segments/{segment.id}/',
            {'text': 'tampered'}, format='json',
        )

        self.assertEqual(response.status_code, 404)
        segment.refresh_from_db()
        self.assertEqual(segment.text, 'mine')

    def test_ownerless_legacy_jobs_are_not_visible_to_ordinary_users(self):
        """
        The rows that predate ownership must not become world-readable by
        treating "no owner" as "everyone" -- that is the leak itself.
        """
        self.client.force_authenticate(self.bob)

        listed = self.client.get('/api/v1/transcriptions/')
        direct = self.client.get(f'/api/v1/transcriptions/{self.orphan_job.id}/')

        self.assertNotIn(str(self.orphan_job.id), self._ids(listed))
        self.assertEqual(direct.status_code, 404)

    # -- administrators -------------------------------------------------- #

    def test_an_administrator_sees_every_job_including_ownerless_ones(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get('/api/v1/transcriptions/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._ids(response),
            {str(self.alice_job.id), str(self.orphan_job.id)},
        )

    # -- ownership is stamped on creation -------------------------------- #

    def test_creating_a_job_records_who_created_it(self):
        self.client.force_authenticate(self.alice)

        response = self.client.post('/api/v1/transcriptions/', {
            'project': str(self.project.id),
            'input_source': 'url',
            'source_url': 'https://example.test/new.mp3',
        }, format='json')

        self.assertIn(response.status_code, (200, 201), getattr(response, 'data', response))
        created = TranscriptionJob.objects.get(pk=response.data['id'])
        self.assertEqual(created.owner_id, self.alice.id)


class TranscriptionSocketAuthorizationTestCase(TransactionTestCase):
    """
    The socket must answer the same question as the API, through the same
    function. An anonymous connection was previously accepted outright.
    """

    def setUp(self):
        self.project = Project.objects.create(name='Socket project')
        self.alice = User.objects.create_user(username='alice', password=PASSWORD)
        self.bob = User.objects.create_user(username='bob', password=PASSWORD)
        self.job = TranscriptionJob.objects.create(
            project=self.project, input_source='url',
            source_url='https://example.test/a.mp3', owner=self.alice,
        )

    async def _connect(self, user):
        from channels.testing import WebsocketCommunicator

        from .consumers import TranscriptionConsumer

        communicator = WebsocketCommunicator(
            TranscriptionConsumer.as_asgi(), f'/ws/transcription/{self.job.id}/',
        )
        communicator.scope['url_route'] = {'kwargs': {'job_id': str(self.job.id)}}
        communicator.scope['user'] = user
        connected, detail = await communicator.connect()
        return communicator, connected, detail

    async def test_an_anonymous_socket_is_refused(self):
        communicator, connected, code = await self._connect(AnonymousUser())

        self.assertFalse(connected, 'an anonymous connection was accepted')
        self.assertEqual(code, 4401)
        await communicator.disconnect()

    async def test_another_users_socket_is_refused(self):
        communicator, connected, code = await self._connect(self.bob)

        self.assertFalse(connected)
        # Same code whether the job is someone else's or absent: telling
        # them apart would confirm which job ids are real.
        self.assertEqual(code, 4403)
        await communicator.disconnect()

    async def test_the_owner_can_watch_their_own_job(self):
        communicator, connected, _ = await self._connect(self.alice)

        self.assertTrue(connected)
        await communicator.disconnect()


class SegmentSpeakerEditTestCase(APITestCase):
    """
    Reassigning one turn to a different speaker.

    Diarization gets turn boundaries wrong as a matter of course, and on an
    Eagle transcription task correcting that IS the graded work. A global
    rename cannot fix it: the error is per-segment, part of one person's
    speech attributed to another.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='ops', password=PASSWORD, is_staff=True)
        self.client.force_authenticate(self.user)
        self.project = Project.objects.create(name='Speakers')
        self.job = TranscriptionJob.objects.create(
            project=self.project, input_source='url',
            source_url='https://example.test/a.mp3', owner=self.user,
            enable_diarization=True,
        )
        self.segment = TranscriptSegment.objects.create(
            job=self.job, start_time=10.0, end_time=20.0,
            speaker_label='Male_1', text='originally attributed to Male_1',
        )
        self.url = (f'/api/v1/transcriptions/{self.job.id}'
                    f'/segments/{self.segment.id}/')

    def test_a_turn_can_be_reassigned_to_another_speaker(self):
        response = self.client.patch(self.url, {'speaker_label': 'Female_1'},
                                     format='json')

        self.assertEqual(response.status_code, 200)
        self.segment.refresh_from_db()
        self.assertEqual(self.segment.speaker_label, 'Female_1')
        # The words were not touched.
        self.assertEqual(self.segment.text, 'originally attributed to Male_1')

    def test_text_and_speaker_can_move_together(self):
        response = self.client.patch(
            self.url, {'text': 'corrected words', 'speaker_label': 'Male_2'},
            format='json')

        self.assertEqual(response.status_code, 200)
        self.segment.refresh_from_db()
        self.assertEqual(self.segment.text, 'corrected words')
        self.assertEqual(self.segment.speaker_label, 'Male_2')

    def test_timestamps_stay_immutable_and_the_attempt_is_refused(self):
        """
        Eagle grades timing. A client that thinks it moved a timestamp must
        be told plainly that it did not, rather than have it ignored.
        """
        response = self.client.patch(
            self.url, {'start_time': 99.0}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('cannot be edited', response.data['error'])
        self.segment.refresh_from_db()
        self.assertEqual(self.segment.start_time, 10.0)

    def test_an_empty_patch_is_refused(self):
        response = self.client.patch(self.url, {}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_editing_still_works_for_text_alone(self):
        # The original behaviour must survive the widening.
        response = self.client.patch(self.url, {'text': 'just the words'},
                                     format='json')

        self.assertEqual(response.status_code, 200)
        self.segment.refresh_from_db()
        self.assertEqual(self.segment.text, 'just the words')
        self.assertEqual(self.segment.speaker_label, 'Male_1')

    def test_another_users_segment_is_still_unreachable(self):
        other = User.objects.create_user(username='bob', password=PASSWORD)
        self.client.force_authenticate(other)

        response = self.client.patch(self.url, {'speaker_label': 'Male_9'},
                                     format='json')

        self.assertEqual(response.status_code, 404)
        self.segment.refresh_from_db()
        self.assertEqual(self.segment.speaker_label, 'Male_1')
