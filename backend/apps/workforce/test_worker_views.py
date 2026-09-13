"""
The tasker's endpoints.

WHAT THESE PROTECT:

  * Audio is served through US. A worker never gets an Eagle URL or a
    cookie, and cannot hear a task they do not hold.
  * Range requests work, because clicking a transcript line to hear that
    moment is the affordance that makes reviewing at speed possible.
  * "Start next" prefers a WARM task -- claiming something unprocessed
    starts the one-hour clock on work the machine has not begun.
  * Economics stay admin-only. A worker sees whether there is work; never
    what it pays or what the balance is.
"""
import os
import tempfile
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.transcriptions.models import Project, TranscriptionJob, TranscriptSegment

from . import states, warmpool
from .credentials import EagleCredential, TRANSLATION
from .eagle_claim import InsufficientMinutes
from .models import Task
from .tests import make_task, make_worker

AUDIO = b'ID3' + b'x' * 4096


class WorkerEndpointBase(APITestCase):

    def setUp(self):
        super().setUp()
        self._cache = tempfile.mkdtemp(prefix='worker-views-')
        patcher = override_settings(EAGLE_MEDIA_CACHE_DIR=self._cache)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.ana_user, self.ana = make_worker('ana', 'W-01')
        self.bo_user, self.bo = make_worker('bo', 'W-02')
        self.admin_user, _ = make_worker('boss', 'A-01', is_admin=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._cache, ignore_errors=True)
        super().tearDown()

    def warm_task(self, claimed_by=None, status=states.NEW, with_audio=True,
                  external_id='4333', seconds=600):
        project = Project.objects.create(name=f'p-{external_id}')
        job = TranscriptionJob.objects.create(
            project=project, input_source='upload', status='completed',
            enable_diarization=True,
        )
        for i in range(8):
            TranscriptSegment.objects.create(
                job=job, start_time=i * 60.0, end_time=(i + 1) * 60.0,
                speaker_label='Male_1',
                text=('the quick brown fox jumps over the lazy dog ' * 8).strip(),
            )
        task = make_task(
            external_source='transcription', external_task_id=external_id,
            duration_seconds=seconds, status=status, job=job,
            claimed_by=claimed_by, title='transcription job',
        )
        if with_audio:
            with open(warmpool.cache_path_for(task), 'wb') as handle:
                handle.write(AUDIO)
        return task


class MediaBoundaryTestCase(WorkerEndpointBase):

    def test_a_worker_can_hear_a_task_they_hold(self):
        task = self.warm_task(claimed_by=self.ana, status=states.IN_PROGRESS)
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(f'/api/v1/workforce/tasks/{task.id}/media/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'audio/mpeg')
        self.assertEqual(response['Accept-Ranges'], 'bytes')

    def test_a_worker_cannot_hear_another_workers_task(self):
        task = self.warm_task(claimed_by=self.bo, status=states.IN_PROGRESS)
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(f'/api/v1/workforce/tasks/{task.id}/media/')

        self.assertEqual(response.status_code, 404)

    def test_an_anonymous_caller_gets_nothing(self):
        task = self.warm_task(claimed_by=self.ana, status=states.IN_PROGRESS)

        response = self.client.get(f'/api/v1/workforce/tasks/{task.id}/media/')

        self.assertIn(response.status_code, (401, 403))

    def test_the_response_carries_no_eagle_url_and_no_cookie(self):
        """
        The boundary is about who we expose, not about what happens to be
        reachable -- transcription audio is public on Eagle and STILL must
        not be handed over directly.
        """
        task = self.warm_task(claimed_by=self.ana, status=states.IN_PROGRESS)
        EagleCredential(product=TRANSLATION).set_cookie('secret=abc')
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(f'/api/v1/workforce/tasks/{task.id}/media/')
        headers = ' '.join(f'{k}: {v}' for k, v in response.items())

        self.assertNotIn('eaglewebcommerce', headers)
        self.assertNotIn('Cookie', headers)
        self.assertNotIn('secret', headers)

    def test_a_range_request_returns_only_that_slice(self):
        task = self.warm_task(claimed_by=self.ana, status=states.IN_PROGRESS)
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(
            f'/api/v1/workforce/tasks/{task.id}/media/', HTTP_RANGE='bytes=100-199')

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response['Content-Length'], '100')
        self.assertEqual(response['Content-Range'], f'bytes 100-199/{len(AUDIO)}')

    def test_an_open_ended_range_runs_to_the_end(self):
        task = self.warm_task(claimed_by=self.ana, status=states.IN_PROGRESS)
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(
            f'/api/v1/workforce/tasks/{task.id}/media/', HTTP_RANGE='bytes=4000-')

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response['Content-Range'], f'bytes 4000-{len(AUDIO)-1}/{len(AUDIO)}')

    def test_a_range_past_the_end_is_refused_properly(self):
        task = self.warm_task(claimed_by=self.ana, status=states.IN_PROGRESS)
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(
            f'/api/v1/workforce/tasks/{task.id}/media/', HTTP_RANGE='bytes=99999-')

        self.assertEqual(response.status_code, 416)

    def test_missing_audio_says_so_rather_than_crashing(self):
        task = self.warm_task(claimed_by=self.ana, status=states.IN_PROGRESS,
                              with_audio=False)
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(f'/api/v1/workforce/tasks/{task.id}/media/')

        self.assertEqual(response.status_code, 404)


class StartNextTaskTestCase(WorkerEndpointBase):

    def test_it_hands_out_a_warm_task_and_claims_it(self):
        self.warm_task()
        self.client.force_authenticate(self.ana_user)

        with mock.patch('apps.workforce.worker_views.claim_for_worker') as claim:
            claim.side_effect = lambda tid, w, a: __import__(
                'apps.workforce.services', fromlist=['services']
            ).claim_task(tid, worker=w, actor=a)
            response = self.client.post('/api/v1/workforce/work/next/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], states.CLAIMED)

    def test_an_unprocessed_task_is_not_handed_out_by_default(self):
        """
        Claiming a cold task starts the hour on work the machine has not
        begun -- the exact failure the warm pool exists to prevent.
        """
        make_task(external_source='transcription', external_task_id='cold',
                  status=states.NEW, duration_seconds=600)
        self.client.force_authenticate(self.ana_user)

        response = self.client.post('/api/v1/workforce/work/next/')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'pool_empty')

    def test_an_empty_pool_says_so_plainly(self):
        self.client.force_authenticate(self.ana_user)

        response = self.client.post('/api/v1/workforce/work/next/')

        self.assertEqual(response.status_code, 409)
        self.assertIn('try again', response.data['error'].lower())

    def test_exhausted_minutes_are_reported_as_an_operator_problem(self):
        self.warm_task()
        self.client.force_authenticate(self.ana_user)

        with mock.patch('apps.workforce.worker_views.claim_for_worker',
                        side_effect=InsufficientMinutes(
                            'no minutes', 'There are not enough platform minutes '
                                          'to start this task. An administrator '
                                          'has been notified.')):
            response = self.client.post('/api/v1/workforce/work/next/')

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'no_minutes')
        self.assertIn('administrator', response.data['error'].lower())

    def test_the_shortest_warm_task_goes_first(self):
        self.warm_task(external_id='long', seconds=1500)
        self.warm_task(external_id='short', seconds=120)
        self.client.force_authenticate(self.ana_user)

        with mock.patch('apps.workforce.worker_views.claim_for_worker') as claim:
            claim.side_effect = lambda tid, w, a: __import__(
                'apps.workforce.services', fromlist=['services']
            ).claim_task(tid, worker=w, actor=a)
            response = self.client.post('/api/v1/workforce/work/next/')

        self.assertEqual(response.data['external_task_id'], 'short')


class PreviewAndSubmitTestCase(WorkerEndpointBase):

    def test_the_preview_shows_the_worker_exactly_what_will_be_sent(self):
        task = self.warm_task(claimed_by=self.ana, status=states.READY_TO_SUBMIT)
        # Not what this test is about -- see run_quality_gate's
        # 'self-check-not-completed' finding, covered on its own below.
        task.accuracy_check_by = self.ana
        task.accuracy_check_at = timezone.now()
        task.save(update_fields=['accuracy_check_by', 'accuracy_check_at'])
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(f'/api/v1/workforce/tasks/{task.id}/preview/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Transcription Job:', response.data['content'])
        self.assertIn('[END OF RECORDING', response.data['content'])
        self.assertFalse(response.data['blocking'])

    def test_a_worker_cannot_preview_another_workers_task(self):
        task = self.warm_task(claimed_by=self.bo, status=states.READY_TO_SUBMIT)
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(f'/api/v1/workforce/tasks/{task.id}/preview/')

        self.assertEqual(response.status_code, 404)

    def test_a_worker_cannot_force_past_the_quality_gate(self):
        """
        `force` is the T-2 endgame, an administrative or automated call --
        not a button that lets a worker skip the gate on ordinary work.
        """
        task = self.warm_task(claimed_by=self.ana, status=states.READY_TO_SUBMIT)
        self.client.force_authenticate(self.ana_user)

        with mock.patch('apps.workforce.worker_views.submit_task') as submit:
            submit.return_value = task
            self.client.post(
                f'/api/v1/workforce/tasks/{task.id}/submit-external/',
                {'force': True}, format='json')

        self.assertIs(submit.call_args.kwargs['force'], False)

    def test_an_administrator_may_force(self):
        task = self.warm_task(claimed_by=self.ana, status=states.READY_TO_SUBMIT)
        self.client.force_authenticate(self.admin_user)

        with mock.patch('apps.workforce.worker_views.submit_task') as submit:
            submit.return_value = task
            self.client.post(
                f'/api/v1/workforce/tasks/{task.id}/submit-external/',
                {'force': True}, format='json')

        self.assertIs(submit.call_args.kwargs['force'], True)

    def test_a_platform_failure_is_a_502_not_a_silent_success(self):
        from .eagle_submit import SubmissionFailed

        task = self.warm_task(claimed_by=self.ana, status=states.READY_TO_SUBMIT)
        self.client.force_authenticate(self.ana_user)

        with mock.patch('apps.workforce.worker_views.submit_task',
                        side_effect=SubmissionFailed('down', 'The platform could not be reached.')):
            response = self.client.post(
                f'/api/v1/workforce/tasks/{task.id}/submit-external/')

        self.assertEqual(response.status_code, 502)
        task.refresh_from_db()
        self.assertEqual(task.status, states.READY_TO_SUBMIT)


class QueueStatusTestCase(WorkerEndpointBase):

    def test_a_worker_sees_whether_there_is_work_and_nothing_about_money(self):
        self.warm_task()
        credential = EagleCredential(product=TRANSLATION)
        credential.set_cookie('a=b')
        credential.minutes_balance = 15
        credential.save()
        self.client.force_authenticate(self.ana_user)

        response = self.client.get('/api/v1/workforce/work/status/')
        body = str(response.data)

        self.assertEqual(response.data['ready_now'], 1)
        self.assertNotIn('minutes_balance', body)
        self.assertNotIn('platform', response.data)
        # The economics stay off the tasker's screen entirely, including
        # the derived "out of minutes" flag.
        self.assertNotIn('minutes_exhausted', response.data)

    def test_an_administrator_sees_the_balance_and_session_health(self):
        credential = EagleCredential(product=TRANSLATION)
        credential.set_cookie('a=b')
        credential.minutes_balance = 15
        credential.save()
        self.client.force_authenticate(self.admin_user)

        response = self.client.get('/api/v1/workforce/work/status/')

        self.assertIn('platform', response.data)
        self.assertEqual(response.data['platform'][0]['minutes_balance'], 15)
        # A healthy balance: not exhausted.
        self.assertFalse(response.data['minutes_exhausted'])

    def test_an_admin_is_told_when_ready_work_cannot_be_claimed_for_no_minutes(self):
        self.warm_task()  # there IS ready work
        credential = EagleCredential(product=TRANSLATION)
        credential.set_cookie('a=b')
        credential.minutes_balance = 0
        credential.save()
        self.client.force_authenticate(self.admin_user)

        response = self.client.get('/api/v1/workforce/work/status/')

        self.assertTrue(response.data['minutes_exhausted'])

    def test_zero_minutes_is_not_flagged_when_there_is_no_ready_work_to_block(self):
        credential = EagleCredential(product=TRANSLATION)
        credential.set_cookie('a=b')
        credential.minutes_balance = 0
        credential.save()
        self.client.force_authenticate(self.admin_user)

        response = self.client.get('/api/v1/workforce/work/status/')

        self.assertEqual(response.data['ready_now'], 0)
        self.assertFalse(response.data['minutes_exhausted'])

    def test_an_expired_session_at_zero_is_a_session_problem_not_a_minutes_one(self):
        self.warm_task()
        credential = EagleCredential(product=TRANSLATION)
        credential.set_cookie('a=b')
        credential.minutes_balance = 0
        credential.status = EagleCredential.EXPIRED
        credential.save()
        self.client.force_authenticate(self.admin_user)

        response = self.client.get('/api/v1/workforce/work/status/')

        # The dead-session banner covers this; don't double-count it as a
        # minutes failure when the balance may just be unreadable.
        self.assertFalse(response.data['minutes_exhausted'])


class BrowsableQueueOrderTestCase(WorkerEndpointBase):
    """
    THE QUEUE MUST LEAD WITH WORK THAT CAN START IMMEDIATELY.

    Claiming a task starts Eagle's one-hour clock. If a cold task sits above
    a warm one, a tasker spends part of that hour waiting for the machine --
    which is the entire reason warming exists. `is_ready` is what lets them
    see the difference before they commit.
    """

    def cold_task(self, external_id='9001', kind=Task.TRANSCRIPTION):
        return make_task(
            kind=kind, external_source='transcription',
            external_task_id=external_id, duration_seconds=600,
            status=states.NEW, title='not yet processed',
        )

    def test_ready_work_sorts_above_cold_work(self):
        self.cold_task(external_id='9001')
        self.warm_task(external_id='4333')
        self.client.force_authenticate(self.ana_user)

        response = self.client.get('/api/v1/workforce/tasks/?available=true')

        self.assertEqual(response.status_code, 200)
        ids = [row['external_task_id'] for row in response.data]
        self.assertLess(ids.index('4333'), ids.index('9001'))

    def test_is_ready_tells_the_worker_which_is_which(self):
        self.cold_task(external_id='9001')
        self.warm_task(external_id='4333')
        self.client.force_authenticate(self.ana_user)

        rows = {row['external_task_id']: row for row
                in self.client.get('/api/v1/workforce/tasks/?available=true').data}

        self.assertTrue(rows['4333']['is_ready'])
        self.assertFalse(rows['9001']['is_ready'])

    def test_a_translation_is_not_ready_until_it_is_translated(self):
        """
        A transcript alone is not the deliverable of a translation task.
        Advertising it as ready would send a worker to a workspace showing
        English text under a French heading.
        """
        task = self.warm_task(external_id='4334')
        task.kind = Task.TRANSLATION
        task.save(update_fields=['kind'])
        self.client.force_authenticate(self.ana_user)

        rows = {row['external_task_id']: row for row
                in self.client.get('/api/v1/workforce/tasks/?available=true').data}

        self.assertFalse(rows['4334']['is_ready'])


class TranslationDraftTestCase(WorkerEndpointBase):
    """
    THE BUG THIS ENDPOINT EXISTS TO FIX.

    The review screen rendered `job.segments`, which on a translation task
    are the ENGLISH source, while the submitter rendered the FRENCH
    revision. A worker looked at one document, edited it, and submitted
    another -- their edits went nowhere and nothing said so.

    Both documents are returned here, aligned by index, and the worker's
    polished version is stored as prose because Eagle's translation
    deliverable has no timestamps and no speaker labels to realign.
    """

    def translation_task(self, claimed_by=None, status=states.IN_PROGRESS):
        from apps.transcriptions.models import AIRevision, AIRevisionSegment

        task = self.warm_task(claimed_by=claimed_by, status=status,
                              external_id='4400')
        task.kind = Task.TRANSLATION
        task.target_language = 'fr'
        task.save(update_fields=['kind', 'target_language'])

        revision = AIRevision.objects.create(
            job=task.job, operation='translate', label='Translation (fr)',
            source_language='en', target_language='fr', status='completed')
        for segment in task.job.segments.all().order_by('start_time'):
            AIRevisionSegment.objects.create(
                revision=revision, original_segment=segment,
                speaker_label='', start_time=segment.start_time,
                end_time=segment.end_time, original_text=segment.text,
                processed_text='Le renard brun saute par-dessus le chien.',
                changed=True)
        Task.objects.filter(pk=task.pk).update(deliverable_revision=revision)
        task.refresh_from_db()
        return task

    def test_the_prompts_travel_with_the_documents(self):
        """
        THE SCREEN USED TO NAME A PROMPT IT DID NOT SUPPLY.

        It told workers to paste the text "under the polish prompt" and gave
        them only the text, so six workers brought six prompts to six
        assistants -- one standard per worker, discovered when the reviews
        came back. The prompt is part of the payload for the same reason the
        French is: the worker should not have to supply it.
        """
        task = self.translation_task(claimed_by=self.ana)
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(f'/api/v1/workforce/tasks/{task.id}/translation/')

        prompts = response.data['prompts']
        self.assertEqual(set(prompts), {'translate', 'polish', 'review'})
        # The rule that cost a submission on task 4813.
        self.assertIn('NEVER "16,67"', prompts['polish'])
        # The check prompt carries the English with it, so the worker pastes
        # one block rather than two in the right order.
        self.assertIn('quick brown fox', prompts['review'])
        self.assertIn('DO NOT REWRITE', prompts['review'])

    def test_both_documents_come_back_aligned(self):
        task = self.translation_task(claimed_by=self.ana)
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(f'/api/v1/workforce/tasks/{task.id}/translation/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['source']), len(response.data['draft']))
        self.assertIn('quick brown fox', response.data['source'][0]['text'])
        self.assertIn('renard brun', response.data['draft'][0]['text'])

    def test_the_pool_can_be_read_but_not_written(self):
        """Deciding whether to take a task means reading it. Writing to one
        you do not hold is a different thing entirely."""
        task = self.translation_task(claimed_by=None, status=states.NEW)
        self.client.force_authenticate(self.bo_user)

        self.assertEqual(
            self.client.get(f'/api/v1/workforce/tasks/{task.id}/translation/').status_code,
            200)
        self.assertEqual(
            self.client.post(f'/api/v1/workforce/tasks/{task.id}/translation/',
                             {'text': 'Bonjour'}, format='json').status_code,
            404)

    def test_a_worker_cannot_touch_another_workers_task(self):
        task = self.translation_task(claimed_by=self.ana)
        self.client.force_authenticate(self.bo_user)

        response = self.client.post(
            f'/api/v1/workforce/tasks/{task.id}/translation/',
            {'text': 'Bonjour'}, format='json')

        self.assertEqual(response.status_code, 404)

    @override_settings(WORKFORCE_TRAINING_MODE=True)
    def test_training_mode_lets_the_open_pool_write(self):
        """
        See WORKFORCE_TRAINING_MODE's own docstring in settings.py. The
        thing this must NOT do: open write access to a task someone else
        holds -- see the next test.
        """
        from .models import TaskEvent

        task = self.translation_task(claimed_by=None, status=states.NEW)
        self.client.force_authenticate(self.bo_user)

        response = self.client.post(
            f'/api/v1/workforce/tasks/{task.id}/translation/',
            {'text': 'Bonjour tout le monde. ' * 100}, format='json')

        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.polished_by_id, self.bo.id)
        event = TaskEvent.objects.get(
            task=task, event_type=TaskEvent.TRANSLATION_POLISHED)
        self.assertTrue(event.metadata.get('training_mode'))

    @override_settings(WORKFORCE_TRAINING_MODE=True)
    def test_training_mode_still_refuses_a_task_someone_else_holds(self):
        """Training mode opens the OPEN POOL, not every task -- a real
        holder's task must stay exactly as protected as before."""
        task = self.translation_task(claimed_by=self.ana)
        self.client.force_authenticate(self.bo_user)

        response = self.client.post(
            f'/api/v1/workforce/tasks/{task.id}/translation/',
            {'text': 'Bonjour'}, format='json')

        self.assertEqual(response.status_code, 404)

    def test_saving_returns_the_gate_verdict_immediately(self):
        """The point of storing it is that it gets checked. Waiting until
        submission to find out costs the worker their hour."""
        task = self.translation_task(claimed_by=self.ana)
        self.client.force_authenticate(self.ana_user)

        response = self.client.post(
            f'/api/v1/workforce/tasks/{task.id}/translation/',
            {'text': 'Le renard brun saute. ' * 200}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIn('findings', response.data)
        self.assertIn('blocking', response.data)

    def test_an_empty_paste_is_refused(self):
        task = self.translation_task(claimed_by=self.ana)
        self.client.force_authenticate(self.ana_user)

        response = self.client.post(
            f'/api/v1/workforce/tasks/{task.id}/translation/',
            {'text': '   '}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_the_polished_version_is_what_gets_submitted(self):
        """It is the later, human-approved version of the same document.
        Rendering the machine draft instead would submit work nobody read."""
        from apps.workforce.eagle_submit import render_for_eagle

        task = self.translation_task(claimed_by=self.ana)
        self.client.force_authenticate(self.ana_user)
        polished = 'Une phrase polie par le tasker. ' * 100

        self.client.post(f'/api/v1/workforce/tasks/{task.id}/translation/',
                         {'text': polished}, format='json')
        task.refresh_from_db()
        document = render_for_eagle(task)

        self.assertIn('polie par le tasker', document)
        self.assertNotIn('renard brun', document)

    def test_it_is_recorded_who_polished_it(self):
        task = self.translation_task(claimed_by=self.ana)
        self.client.force_authenticate(self.ana_user)

        self.client.post(f'/api/v1/workforce/tasks/{task.id}/translation/',
                         {'text': 'Bonjour tout le monde. ' * 100}, format='json')
        task.refresh_from_db()

        self.assertEqual(task.polished_by_id, self.ana.id)
        self.assertIsNotNone(task.polished_at)


class TranscriptCheckPromptTestCase(WorkerEndpointBase):
    """
    GET /workforce/tasks/<id>/transcript-check-prompt/ -- see
    TranscriptCheckPromptView's own docstring for the gap this closes.
    """

    def test_a_worker_holding_the_task_gets_the_prompt(self):
        task = self.warm_task(claimed_by=self.ana, status=states.IN_PROGRESS)
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(
            f'/api/v1/workforce/tasks/{task.id}/transcript-check-prompt/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Report only', response.data['prompt'])

    def test_a_translation_task_has_no_transcript_check_prompt(self):
        """A different job needs a different prompt -- worker_prompts.build
        already covers translation, and this endpoint is not it."""
        task = self.warm_task(claimed_by=self.ana, status=states.IN_PROGRESS)
        task.kind = Task.TRANSLATION
        task.save(update_fields=['kind'])
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(
            f'/api/v1/workforce/tasks/{task.id}/transcript-check-prompt/')

        self.assertEqual(response.status_code, 404)

    def test_a_worker_cannot_read_another_workers_task(self):
        task = self.warm_task(claimed_by=self.bo, status=states.IN_PROGRESS)
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(
            f'/api/v1/workforce/tasks/{task.id}/transcript-check-prompt/')

        self.assertEqual(response.status_code, 404)

    def test_the_prompt_matches_the_projects_own_standard(self):
        """THE STANDARD INVERTS THE INSTRUCTION. A task filed under a
        strict-verbatim project must not be told to flag contractions --
        that would ask the worker to break the standard the task is graded
        on."""
        task = self.warm_task(claimed_by=self.ana, status=states.IN_PROGRESS)
        task.project = 'Legal Dictation'
        task.save(update_fields=['project'])
        self.client.force_authenticate(self.ana_user)

        response = self.client.get(
            f'/api/v1/workforce/tasks/{task.id}/transcript-check-prompt/')

        self.assertIn('do NOT flag any of these', response.data['prompt'])

    def test_a_worker_can_record_what_their_assistant_found(self):
        """
        THE AUDIT TRAIL, NOT A REWRITE. Posting notes must not touch the
        transcript itself -- only Task.accuracy_check_notes/by/at, which
        is what run_quality_gate's 'self-check-not-completed' finding
        checks for.
        """
        task = self.warm_task(claimed_by=self.ana, status=states.IN_PROGRESS)
        self.client.force_authenticate(self.ana_user)

        response = self.client.post(
            f'/api/v1/workforce/tasks/{task.id}/transcript-check-prompt/',
            {'notes': 'Checked "Robert Solow" against the audio -- correct.'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['saved'])
        task.refresh_from_db()
        self.assertEqual(task.accuracy_check_by_id, self.ana.id)
        self.assertIsNotNone(task.accuracy_check_at)
        self.assertIn('Robert Solow', task.accuracy_check_notes)

    def test_saving_the_check_clears_the_submission_gate(self):
        """The whole point of storing this: preview_submission's verdict,
        handed back immediately, no longer carries the finding."""
        task = self.warm_task(claimed_by=self.ana, status=states.READY_TO_SUBMIT)
        self.client.force_authenticate(self.ana_user)

        response = self.client.post(
            f'/api/v1/workforce/tasks/{task.id}/transcript-check-prompt/',
            {'notes': 'Nothing to report.'},
        )

        codes = {f.get('code') for f in response.data['findings']}
        self.assertNotIn('self-check-not-completed', codes)

    def test_an_empty_paste_back_is_refused(self):
        task = self.warm_task(claimed_by=self.ana, status=states.IN_PROGRESS)
        self.client.force_authenticate(self.ana_user)

        response = self.client.post(
            f'/api/v1/workforce/tasks/{task.id}/transcript-check-prompt/',
            {'notes': '   '},
        )

        self.assertEqual(response.status_code, 400)
        task.refresh_from_db()
        self.assertIsNone(task.accuracy_check_by_id)

    def test_a_worker_cannot_record_a_check_on_another_workers_task(self):
        """Reading the prompt is allowed from the open pool; recording that
        the check was run is not -- see the POST docstring."""
        task = self.warm_task(claimed_by=self.bo, status=states.IN_PROGRESS)
        self.client.force_authenticate(self.ana_user)

        response = self.client.post(
            f'/api/v1/workforce/tasks/{task.id}/transcript-check-prompt/',
            {'notes': 'Nothing to report.'},
        )

        self.assertEqual(response.status_code, 404)

    @override_settings(WORKFORCE_TRAINING_MODE=True)
    def test_training_mode_lets_the_open_pool_record_a_check(self):
        from .models import TaskEvent

        task = self.warm_task(claimed_by=None, status=states.NEW)
        self.client.force_authenticate(self.bo_user)

        response = self.client.post(
            f'/api/v1/workforce/tasks/{task.id}/transcript-check-prompt/',
            {'notes': 'Checked against the audio -- correct.'},
        )

        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.accuracy_check_by_id, self.bo.id)
        event = TaskEvent.objects.get(
            task=task, event_type=TaskEvent.ACCURACY_CHECK_COMPLETED)
        self.assertTrue(event.metadata.get('training_mode'))

    @override_settings(WORKFORCE_TRAINING_MODE=True)
    def test_training_mode_still_refuses_a_task_someone_else_holds(self):
        task = self.warm_task(claimed_by=self.ana, status=states.IN_PROGRESS)
        self.client.force_authenticate(self.bo_user)

        response = self.client.post(
            f'/api/v1/workforce/tasks/{task.id}/transcript-check-prompt/',
            {'notes': 'Nothing to report.'},
        )

        self.assertEqual(response.status_code, 404)

    def test_a_translation_task_refuses_the_paste_back_too(self):
        task = self.warm_task(claimed_by=self.ana, status=states.IN_PROGRESS)
        task.kind = Task.TRANSLATION
        task.save(update_fields=['kind'])
        self.client.force_authenticate(self.ana_user)

        response = self.client.post(
            f'/api/v1/workforce/tasks/{task.id}/transcript-check-prompt/',
            {'notes': 'Nothing to report.'},
        )

        self.assertEqual(response.status_code, 404)
