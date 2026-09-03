"""
The whole flow, narrated, against a FAKE Eagle.

Run it to watch what happens end to end without touching the platform:

    ../.venv/Scripts/python.exe manage.py test --noinput \\
        apps.workforce.test_walkthrough -v 2

WHY THIS EXISTS AS A TEST RATHER THAN A SCRIPT. It needs a database and it
must never reach Eagle. The test runner gives it a throwaway database and
this module substitutes a fake service, so there is no path by which it can
claim a real task, spend a real minute, or submit real work -- which a
script pointed at the live database could do by accident.

It is also a real test: every step asserts. If the narration prints, the
flow works.
"""
import io
import os
import tempfile
from contextlib import redirect_stdout
from unittest import mock

from django.test import TestCase, override_settings

from apps.transcriptions.models import Project, TranscriptionJob, TranscriptSegment

from . import services, states, warmpool
from .eagle import (
    EagleIntegrationService, EagleMedia, EagleSubmissionResult, EagleTask,
)
from .models import Task
from .tests import make_worker

AUDIO = b'ID3' + b'\x00' * 8192


class FakeEagle(EagleIntegrationService):
    """
    Eagle, as far as this walkthrough is concerned.

    It holds a list of task ids and can be told to take one away, which is
    how the "somebody else claimed it" case is exercised without waiting for
    a real worker on the real platform to do it.
    """

    def __init__(self):
        self.offered = {
            '37480': ('C 64.mp3', 240),
            '37477': ('C 61.mp3', 190),
        }
        self.submitted = {}
        self.claimed = set()

    def is_configured(self):
        return True

    def check_session(self, product):
        return {'alive': True, 'minutes_balance': 500}

    def list_available_tasks(self, product):
        return [
            EagleTask(
                external_id=task_id, kind='transcription', title=title,
                page_url=f'https://example.invalid/tasks/{task_id}',
                source_language='en', target_language='',
                duration_seconds=seconds, raw={},
            )
            for task_id, (title, seconds) in sorted(self.offered.items())
        ]

    def get_task(self, external_id, source=None):
        title, seconds = self.offered[str(external_id)]
        return EagleTask(
            external_id=str(external_id), kind='transcription', title=title,
            page_url=f'https://example.invalid/tasks/{external_id}',
            source_language='en', target_language='',
            duration_seconds=seconds, raw={},
        )

    def get_task_media(self, external_id, source=None):
        return EagleMedia(url=f'https://example.invalid/a/{external_id}.mp3',
                          headers={})

    def download_media(self, external_id, destination, source=None):
        with open(destination, 'wb') as handle:
            handle.write(AUDIO)
        return destination

    def claim_task(self, external_id, source=None, **extra):
        self.claimed.add(str(external_id))
        return {'accepted': True}

    def submit_task(self, external_id, content, source=None, **extra):
        self.submitted[str(external_id)] = content
        return EagleSubmissionResult(
            accepted=True, reference=f'fake-{external_id}',
            message='accepted by the fake platform')


def say(*parts):
    print(' '.join(str(p) for p in parts))


@override_settings(EAGLE_WARM_QUALITY_PROFILE='none')
class WalkthroughTestCase(TestCase):
    """One task, from appearing on Eagle to leaving our queue."""

    def setUp(self):
        super().setUp()
        self.cache = tempfile.mkdtemp(prefix='walkthrough-')
        patcher = override_settings(EAGLE_MEDIA_CACHE_DIR=self.cache)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.addCleanup(
            lambda: __import__('shutil').rmtree(self.cache, ignore_errors=True))

        self.eagle = FakeEagle()
        self.ana_user, self.ana = make_worker('ana', 'W-01')
        self.bo_user, self.bo = make_worker('bo', 'W-02')

    # ----------------------------------------------------------------- #

    def test_the_whole_flow(self):
        from .sync import sync_product

        say('\n1. THE POLLER LOOKS AT EAGLE')
        report = sync_product('transcription', service=self.eagle)
        say('   Eagle is offering', report.seen, 'tasks;',
            report.created, 'were new to us.')
        self.assertEqual(report.created, 2)

        task = Task.objects.get(external_task_id='37480')
        say('   We stored it as', task.external_task_id,
            '- the SAME id Eagle uses, so the two screens line up.')
        say('   Its status is', repr(task.status),
            '- discovered, claimed by nobody, costing nothing.')
        self.assertEqual(task.status, states.NEW)
        self.assertIsNone(task.claimed_by_id)

        say('\n2. A TASKER LOOKS AT THE QUEUE, BEFORE ANY WARMING')
        say('   is_ready?', self._ready(task),
            '- so the card does NOT say "Ready now".')
        self.assertFalse(self._ready(task))
        say('   They can still press Listen first: the audio is fetched')
        say('   long before the transcript exists.')

        say('\n3. THE WARM POOL PRE-PROCESSES IT (nobody has claimed it)')
        self._warm(task)
        task.refresh_from_db()
        say('   A transcript now exists:', task.job.segments.count(), 'segments.')
        say('   is_ready?', self._ready(task), '- the card now says "Ready now"')
        say('   and it sorts ABOVE every cold task in the queue.')
        self.assertTrue(self._ready(task))

        say('\n4. ANA CLAIMS IT')
        services.claim_task(task.id, worker=self.ana, actor=self.ana_user)
        task.refresh_from_db()
        say('   status:', repr(task.status), '  held by:', task.claimed_by.worker_code)
        self.assertEqual(task.claimed_by_id, self.ana.id)

        say("   Bo's queue is told IMMEDIATELY that it is gone -- the")
        say("   broadcast fires when a task LEAVES the pool, not only when")
        say("   it arrives. Bo never sees a Claim button on Ana's task.")
        self.assertNotIn(task, self._pool())

        say('\n5. ANA REVIEWS: audio, speaker labels, text')
        segment = task.job.segments.order_by('start_time').first()
        say('   Machine said:', repr(segment.text[:48]))
        segment.speaker_label = 'Female_1'
        segment.text = 'We were going there.'
        segment.save(update_fields=['speaker_label', 'text'])
        say('   Ana corrected the speaker and the words. Her edit lives on')
        say('   the transcript itself, which is what the submitter renders.')

        say('\n6. SHE PREVIEWS EXACTLY WHAT EAGLE WILL RECEIVE')
        from .eagle_submit import preview_submission
        services.start_task(task.id, actor=self.ana_user)
        services.begin_review(task.id, actor=self.ana_user)
        services.mark_ready(task.id, actor=self.ana_user)
        preview = preview_submission(task)
        say('  ', preview.word_count, 'words,', len(preview.findings), 'findings,',
            'blocking:', preview.blocking)
        for finding in preview.findings:
            say('    -', finding['severity'], '|', finding['message'][:66])
        first = preview.content.splitlines()[0]
        say('   first line:', repr(first))
        say('   Nothing here is blocking, so the Submit button is enabled.')
        self.assertFalse(preview.blocking)

        say('\n7. SUBMIT')
        from .eagle_submit import submit_task
        submit_task(task.id, actor=self.ana_user, service=self.eagle)
        task.refresh_from_db()
        say('   status:', repr(task.status))
        say('   Eagle received', len(self.eagle.submitted['37480']), 'characters.')
        self.assertIn('37480', self.eagle.submitted)

        say('\n8. MEANWHILE, SOMEONE ELSE ON EAGLE TAKES THE OTHER TASK')
        other = Task.objects.get(external_task_id='37477')
        say('   Before:', other.external_task_id, 'is', repr(other.status),
            'and in our queue.')
        self.assertIn(other, self._pool())

        del self.eagle.offered['37477']
        report = sync_product('transcription', service=self.eagle)
        other.refresh_from_db()
        say('   Eagle stops offering it; the next poll retires',
            report.taken_by_others, 'task.')
        say('   After: ', other.external_task_id, 'is', repr(other.status),
            'and GONE from the queue.')
        self.assertEqual(other.status, states.CANCELLED)
        self.assertNotIn(other, self._pool())

        say('\n   That is the whole mechanism. It is only as fresh as the')
        say('   poll interval -- nothing can know Eagle changed until we ask.')
        say('')

    # ----------------------------------------------------------------- #

    @staticmethod
    def _ready(task):
        from .serializers import TaskSerializer
        return TaskSerializer().get_is_ready(task)

    @staticmethod
    def _pool():
        return list(Task.objects.filter(
            status__in=states.CLAIMABLE_STATES,
            claimed_by__isnull=True, assigned_to__isnull=True))

    def _warm(self, task):
        """Stand in for the pipeline: real audio fetch, canned transcript."""
        project, _ = Project.objects.get_or_create(name='Eagle')
        job = TranscriptionJob.objects.create(
            project=project, input_source='upload', status='completed',
            enable_diarization=True)
        for i in range(6):
            TranscriptSegment.objects.create(
                job=job, start_time=i * 40.0, end_time=(i + 1) * 40.0,
                speaker_label='Male_1' if i % 2 == 0 else 'Male_2',
                text='Um, we was going there and it was, you know, fine. ' * 4)
        Task.objects.filter(pk=task.pk).update(job=job)
        with open(warmpool.cache_path_for(task), 'wb') as handle:
            handle.write(AUDIO)
