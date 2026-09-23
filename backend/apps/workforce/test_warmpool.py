"""
The pre-processing warm pool.

WHAT THESE PROTECT:

  * Warming CLAIMS NOTHING. It is the cheap half of the design precisely
    because it spends no minutes; a warm cycle that claimed would turn a
    speculative optimisation into a speculative purchase.
  * The cached audio SURVIVES the pipeline. The pipeline deletes
    `job.file_path` by design, and the worker needs that audio to review
    against -- so the working copy it eats must not be the cache itself.
  * Concurrency is BOUNDED. At roughly 750 MB of models per pipeline, an
    unbounded pool is an out-of-memory waiting for a busy morning.
  * Retention deletes AUDIO and keeps the record.

The transcription pipeline is faked. Running it for real would need ffmpeg,
whisper and a 30 MB download per test.
"""
import os
from unittest import mock

from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.transcriptions.models import (
    AIRevision, AIRevisionSegment, Project, TranscriptionJob, TranscriptSegment,
)
from core.services.ai_assist.quality import (
    EDITED_VERBATIM, STRICT_VERBATIM, get_standard,
)

from . import states, warmpool
from .eagle import EagleIntegrationService, EagleMedia, EagleTask
from .models import Task
from .tests import make_task, make_worker


def eagle_task(external_id='4333', needs_session=False):
    return EagleTask(
        external_id=str(external_id), source='transcription', kind='transcription',
        duration_seconds=600,
        raw={'media_url': f'https://example.test/{external_id}.mp3',
             'media_needs_session': needs_session},
    )


class FakeEagle(EagleIntegrationService):
    def __init__(self, needs_session=False):
        self.needs_session = needs_session
        self.claims = []
        self.authenticated_calls = 0
        self.plain_calls = 0

    def is_configured(self):
        return True

    def get_task(self, external_id, source=''):
        return eagle_task(external_id, self.needs_session)

    def get_task_media(self, external_id, source=''):
        self.plain_calls += 1
        return EagleMedia(url=f'https://example.test/{external_id}.mp3')

    def get_authenticated_media(self, external_id, source=''):
        self.authenticated_calls += 1
        media = EagleMedia(url=f'https://example.test/{external_id}.mp3')
        media.headers = {'Cookie': 'SECRET'}
        return media

    def claim_task(self, external_id, source=''):
        self.claims.append(external_id)
        raise AssertionError('warming must never claim')


class TempCacheMixin:
    """Each test gets its own cache directory; none touches the real one."""

    def setUp(self):
        super().setUp()
        import tempfile
        self._cache = tempfile.mkdtemp(prefix='warmpool-test-')
        patcher = override_settings(EAGLE_MEDIA_CACHE_DIR=self._cache)
        patcher.enable()
        self.addCleanup(patcher.disable)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._cache, ignore_errors=True)
        super().tearDown()


def fake_download(content=b'ID3fake-audio-bytes'):
    """Substitutes the network for `fetch_media`'s streaming download."""
    class _Response:
        def __init__(self):
            self._data = content
        def read(self, *a):
            data, self._data = self._data, b''
            return data
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return mock.patch('urllib.request.urlopen', return_value=_Response())


class MediaCacheTestCase(TempCacheMixin, TestCase):

    def test_public_media_is_fetched_without_a_cookie(self):
        task = make_task(external_source='transcription', external_task_id='4333')
        fake = FakeEagle(needs_session=False)

        with fake_download():
            path = warmpool.fetch_media(task, service=fake)

        self.assertTrue(os.path.exists(path))
        self.assertEqual(fake.authenticated_calls, 0,
                         'asked for credentials on public media')
        self.assertEqual(fake.plain_calls, 1)

    def test_session_gated_media_uses_the_authenticated_door(self):
        task = make_task(external_source='translation', external_task_id='4333')
        fake = FakeEagle(needs_session=True)

        with fake_download():
            warmpool.fetch_media(task, service=fake)

        self.assertEqual(fake.authenticated_calls, 1)

    def test_a_cached_file_is_not_downloaded_again(self):
        task = make_task(external_source='transcription', external_task_id='4333')
        fake = FakeEagle()

        with fake_download():
            warmpool.fetch_media(task, service=fake)
        with fake_download() as second:
            warmpool.fetch_media(task, service=fake)

        self.assertEqual(second.call_count, 0)

    def test_an_interrupted_download_leaves_no_usable_cache_file(self):
        """
        A `.part` promoted to the real name would look cached forever and
        feed a truncated file to the pipeline on every future cycle.
        """
        task = make_task(external_source='transcription', external_task_id='4333')

        with mock.patch('urllib.request.urlopen', side_effect=OSError('connection reset')):
            with self.assertRaises(OSError):
                warmpool.fetch_media(task, service=FakeEagle())

        self.assertFalse(os.path.exists(warmpool.cache_path_for(task)))
        self.assertFalse(os.path.exists(warmpool.cache_path_for(task) + '.part'))


class WorkingCopyTestCase(TempCacheMixin, TestCase):

    def test_the_pipeline_deleting_its_copy_leaves_the_cache_intact(self):
        """
        The trap: the pipeline removes `job.file_path` unconditionally, and
        the worker still needs that audio to review against.
        """
        cache_path = os.path.join(warmpool.cache_dir(), 'cached.mp3')
        with open(cache_path, 'wb') as handle:
            handle.write(b'audio')

        working = warmpool._working_copy(cache_path, 'job-1')
        self.assertTrue(os.path.exists(working))
        self.assertNotEqual(os.path.abspath(working), os.path.abspath(cache_path))

        os.remove(working)  # what the pipeline does

        self.assertTrue(os.path.exists(cache_path), 'the pipeline ate the cache')
        with open(cache_path, 'rb') as handle:
            self.assertEqual(handle.read(), b'audio')


class WarmCycleTestCase(TempCacheMixin, TransactionTestCase):
    """
    TransactionTestCase, not TestCase, because the pool uses threads.

    A wrapped TestCase runs inside one transaction on one connection; a
    worker thread gets its own connection and cannot see -- or be seen by --
    that transaction. Writes made in the pool would simply vanish, and the
    test would be asserting against a fiction.
    """


    def _completed_pipeline(self):
        """Stands in for the real pipeline, marking the job completed."""
        def run(job_id):
            TranscriptionJob.objects.filter(pk=job_id).update(status='completed')
        return mock.patch('apps.transcriptions.tasks.run_transcription_pipeline',
                          side_effect=run)

    def test_warming_never_claims(self):
        make_task(external_source='transcription', external_task_id='1',
                  status=states.NEW, duration_seconds=120)
        fake = FakeEagle()

        with fake_download(), self._completed_pipeline():
            warmpool.run_cycle(pool_size=5, concurrency=1, service=fake)

        self.assertEqual(fake.claims, [], 'the warm pool claimed a task')

    def test_a_warmed_task_gets_a_completed_transcript_and_stays_claimable(self):
        task = make_task(external_source='transcription', external_task_id='1',
                         status=states.NEW, duration_seconds=120)
        fake = FakeEagle()

        with fake_download(), self._completed_pipeline():
            report = warmpool.run_cycle(pool_size=5, concurrency=1, service=fake)

        task.refresh_from_db()
        self.assertEqual(report.processed, 1)
        self.assertIsNotNone(task.job_id)
        self.assertEqual(task.job.status, 'completed')
        # Warming must not move the task: it is still there to be claimed.
        self.assertEqual(task.status, states.NEW)
        self.assertIsNone(task.claimed_by_id)

    def test_the_job_created_for_warming_carries_no_credentials(self):
        make_task(external_source='translation', external_task_id='1',
                  status=states.NEW, duration_seconds=120)

        with fake_download(), self._completed_pipeline():
            warmpool.run_cycle(pool_size=5, concurrency=1,
                               service=FakeEagle(needs_session=True))

        job = TranscriptionJob.objects.first()
        self.assertFalse(job.site_auth_headers,
                         'a cookie was copied onto the job, as the legacy path did')

    def test_shortest_tasks_are_warmed_first(self):
        make_task(external_source='transcription', external_task_id='long',
                  status=states.NEW, duration_seconds=1540)
        make_task(external_source='transcription', external_task_id='short',
                  status=states.NEW, duration_seconds=120)

        picked = warmpool.select_candidates(1)

        self.assertEqual(picked[0].external_task_id, 'short')

    def test_kind_narrows_and_exclude_ids_removes(self):
        translation = make_task(
            kind=Task.TRANSLATION, external_source='translation',
            external_task_id='tr', status=states.NEW,
            duration_seconds=60, target_language='fr')
        transcription = make_task(
            kind=Task.TRANSCRIPTION, external_source='transcription',
            external_task_id='ts', status=states.NEW, duration_seconds=1200)

        only_transcription = warmpool.select_candidates(10, kind=Task.TRANSCRIPTION)
        self.assertEqual([t.id for t in only_transcription], [transcription.id])

        without_it = warmpool.select_candidates(
            10, exclude_ids=[transcription.id])
        self.assertEqual([t.id for t in without_it], [translation.id])

    def test_a_full_pool_does_no_work(self):
        for i in range(3):
            job = TranscriptionJob.objects.create(
                project=None if False else __import__(
                    'apps.transcriptions.models', fromlist=['Project']
                ).Project.objects.create(name=f'p{i}'),
                input_source='upload', status='completed')
            make_task(external_source='transcription', external_task_id=str(i),
                      status=states.NEW, duration_seconds=120, job=job)
        fake = FakeEagle()

        report = warmpool.run_cycle(pool_size=3, concurrency=1, service=fake)

        self.assertEqual(report.already_warm, 3)
        self.assertEqual(report.considered, 0)

    def test_a_transcript_only_translation_does_not_count_as_warm(self):
        """
        Regression test for a real production incident: run_cycle's own
        warm_now query used a looser check than is_warm() (job.status ==
        'completed' alone), so a translation task with a finished
        transcript but no actual translation yet was miscounted as warm.
        Confirmed live: production sat at a constant `[warm] ready=24`
        for 5 hours while the dashboard's real is_warm()-based count
        showed 18, and only 4 real translations completed in that window
        despite 76 unclaimed tasks available -- shortfall was computing
        as 0 against the inflated count, so no new candidates, including
        the stranded half-done ones, were ever pulled.
        """
        job = TranscriptionJob.objects.create(
            project=Project.objects.create(name='half-done'),
            input_source='upload', status='completed',
        )
        # Transcript is done; nothing has translated it yet -- exactly
        # the state select_candidates' own TRANSLATION branch is built to
        # pick back up (job__status='completed', deliverable_revision
        # __isnull=True), which only happens if shortfall > 0.
        make_task(kind=Task.TRANSLATION, external_source='translation',
                 external_task_id='half', status=states.NEW,
                 duration_seconds=120, job=job, target_language='fr')

        report = warmpool.run_cycle(pool_size=1, concurrency=1, service=FakeEagle())

        self.assertEqual(report.already_warm, 0,
                         'a transcript-only translation must not count as warm')
        self.assertEqual(report.considered, 1,
                         'shortfall must stay open so the half-done task gets revisited')

    def test_one_failure_does_not_stop_the_cycle(self):
        make_task(external_source='transcription', external_task_id='bad',
                  status=states.NEW, duration_seconds=120)
        make_task(external_source='transcription', external_task_id='good',
                  status=states.NEW, duration_seconds=240)

        calls = {'n': 0}

        # Signature matches warm_task's, including the stage flag: a fake
        # that is missing it raises TypeError inside the guard, and every
        # task is then counted a failure for the wrong reason.
        def flaky(task, service=None, allow_ai=True):
            calls['n'] += 1
            if task.external_task_id == 'bad':
                raise RuntimeError('download exploded')
            return True

        with mock.patch.object(warmpool, 'warm_task', side_effect=flaky):
            report = warmpool.run_cycle(pool_size=5, concurrency=1, service=FakeEagle())

        self.assertEqual(report.failed, 1)
        self.assertEqual(report.processed, 1)
        self.assertEqual(calls['n'], 2)

    def test_concurrency_is_bounded(self):
        """~750 MB of models per pipeline makes this a memory budget."""
        for i in range(6):
            make_task(external_source='transcription', external_task_id=str(i),
                      status=states.NEW, duration_seconds=120)

        import threading
        live, peak, lock = 0, [0], threading.Lock()

        def slow(task, service=None, allow_ai=True):
            nonlocal live
            with lock:
                live += 1
                peak[0] = max(peak[0], live)
            import time
            time.sleep(0.05)
            with lock:
                live -= 1
            return True

        with mock.patch.object(warmpool, 'warm_task', side_effect=slow):
            warmpool.run_cycle(pool_size=6, concurrency=2, service=FakeEagle())

        self.assertLessEqual(peak[0], 2, f'{peak[0]} pipelines ran at once')

    def test_transcription_gets_a_reserved_share_of_every_cycle(self):
        """
        Regression test for a real production incident: translation posts
        up to twice a day and transcription about once every two, so
        translation's backlog regularly outnumbers pool_size several times
        over. The combined, kind-blind selection let it win every slot,
        every cycle, indefinitely -- confirmed live: 22 transcription
        tasks sat completely untouched (job=None on every one) for over
        22 hours while translation, with 64 unclaimed tasks against a
        pool_size of 24, kept the pool constantly full. Raising pool_size
        alone would not have fixed this -- translation's backlog exceeds
        even a much larger pool -- so TRANSCRIPTION_RESERVE guarantees a
        floor regardless of how lopsided supply or duration gets.
        """
        for i in range(10):
            make_task(kind=Task.TRANSLATION, external_source='translation',
                     external_task_id=f't{i}', status=states.NEW,
                     duration_seconds=60, target_language='fr')
        for i in range(2):
            make_task(kind=Task.TRANSCRIPTION, external_source='transcription',
                     external_task_id=f's{i}', status=states.NEW,
                     duration_seconds=1200)

        processed_kinds = []

        def record(task, service=None, allow_ai=True):
            processed_kinds.append(task.kind)
            return True

        with mock.patch.object(warmpool, 'warm_task', side_effect=record):
            report = warmpool.run_cycle(pool_size=5, concurrency=2, service=FakeEagle())

        self.assertEqual(
            processed_kinds.count(Task.TRANSCRIPTION), 2,
            'both waiting transcription tasks should have been reserved a '
            'slot despite being far longer than every translation candidate',
        )
        self.assertEqual(
            processed_kinds.count(Task.TRANSLATION), 3,
            'the remaining shortfall (5 - 2 reserved) should still go to '
            'translation -- the reserve is a floor, not a monopoly',
        )
        self.assertEqual(report.considered, 5)

    def test_the_reserve_never_exceeds_the_shortfall(self):
        """A pool with only one slot open must not reserve three for
        transcription and overshoot pool_size."""
        for i in range(5):
            make_task(kind=Task.TRANSCRIPTION, external_source='transcription',
                     external_task_id=f's{i}', status=states.NEW,
                     duration_seconds=120)

        processed = []
        with mock.patch.object(warmpool, 'warm_task',
                               side_effect=lambda t, service=None, allow_ai=True:
                               processed.append(t.id) or True):
            report = warmpool.run_cycle(pool_size=1, concurrency=1, service=FakeEagle())

        self.assertEqual(len(processed), 1)
        self.assertEqual(report.considered, 1)

    def test_the_reserve_does_not_starve_translation_when_nothing_is_waiting(self):
        """No transcription candidates means the full shortfall still goes
        to whatever else needs it -- the reserve must not sit idle and
        withhold slots nobody is there to fill."""
        for i in range(4):
            make_task(kind=Task.TRANSLATION, external_source='translation',
                     external_task_id=f't{i}', status=states.NEW,
                     duration_seconds=60, target_language='fr')

        with mock.patch.object(warmpool, 'warm_task', return_value=True):
            report = warmpool.run_cycle(pool_size=4, concurrency=2, service=FakeEagle())

        self.assertEqual(report.considered, 4)


class RetentionTestCase(TempCacheMixin, TestCase):

    def _with_audio(self, task):
        path = warmpool.cache_path_for(task)
        with open(path, 'wb') as handle:
            handle.write(b'audio-bytes')
        return path

    def test_audio_for_a_finished_task_is_deleted_after_the_window(self):
        task = make_task(external_source='transcription', external_task_id='1',
                         status=states.APPROVED)
        path = self._with_audio(task)
        Task.objects.filter(pk=task.pk).update(
            updated_at=timezone.now() - timezone.datetime.resolution
        )
        old = timezone.now() - __import__('datetime').timedelta(days=5)
        Task.objects.filter(pk=task.pk).update(updated_at=old)

        result = warmpool.purge_expired_audio()

        self.assertFalse(os.path.exists(path))
        self.assertGreaterEqual(result['files_removed'], 1)

    def test_recent_finished_work_keeps_its_audio(self):
        """
        A recent rejection is still worth listening to -- a worker or admin
        reviewing why it was rejected wants the audio, even though the
        decision itself is final and the task cannot be reworked.
        """
        task = make_task(external_source='transcription', external_task_id='1',
                         status=states.REJECTED)
        path = self._with_audio(task)

        warmpool.purge_expired_audio()

        self.assertTrue(os.path.exists(path))

    def test_the_task_and_its_history_survive_the_purge(self):
        task = make_task(external_source='transcription', external_task_id='1',
                         status=states.APPROVED)
        self._with_audio(task)
        old = timezone.now() - __import__('datetime').timedelta(days=5)
        Task.objects.filter(pk=task.pk).update(updated_at=old)

        warmpool.purge_expired_audio()

        # Only audio is reclaimed; the record is small and stays.
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())

    def test_audio_for_a_race_we_lost_goes_quickly(self):
        task = make_task(external_source='transcription', external_task_id='1',
                         status=states.CANCELLED)
        path = self._with_audio(task)
        old = timezone.now() - __import__('datetime').timedelta(hours=3)
        Task.objects.filter(pk=task.pk).update(updated_at=old)

        warmpool.purge_expired_audio()

        self.assertFalse(os.path.exists(path))


class StandardInstructionTestCase(TestCase):
    """
    The rubric reaches the model as an instruction derived from the
    standard's own flags. Hand-writing one per standard is how the two
    drift into agreeing with each other.
    """

    def test_the_two_standards_give_opposite_instructions(self):
        edited = warmpool.standard_instruction(get_standard(EDITED_VERBATIM))
        strict = warmpool.standard_instruction(get_standard(STRICT_VERBATIM))

        self.assertIn('Remove filler words', edited)
        self.assertIn('Keep every filler word', strict)

    def test_neither_standard_corrects_grammar_any_more(self):
        """
        CHANGED 2026-09-13. Eagle's General Guidelines' Writing Rules are
        blanket and unconditional -- "do not fix the speaker's grammar" --
        so `correct_grammar` is now False for BOTH standards; only
        `capitalize_sentences` (a separate flag) still tells edited
        verbatim to capitalize sentence starts. See
        TranscriptionStandard's own docstring for why the two were split.
        """
        edited = warmpool.standard_instruction(get_standard(EDITED_VERBATIM))
        strict = warmpool.standard_instruction(get_standard(STRICT_VERBATIM))
        self.assertIn('Correct nothing', edited)
        self.assertIn('Correct nothing', strict)
        self.assertNotIn('Correct grammar, punctuation', edited)

    def test_both_carry_the_format_rules_that_hold_either_way(self):
        for name in (EDITED_VERBATIM, STRICT_VERBATIM):
            text = warmpool.standard_instruction(get_standard(name))
            self.assertIn('Male_1', text)
            self.assertIn('[inaudible]', text)

    def test_the_tag_list_is_imported_not_duplicated(self):
        """
        This line used to be its own hand-typed copy, separate from
        ALLOWED_TAGS in transcription.py -- and it drifted: neither
        mentioned [foreign] or [background], which Eagle's Audio Rules
        name directly. Built from the canonical set now, so it cannot
        drift again.
        """
        from core.services.ai_assist.quality import ALLOWED_TAGS
        text = warmpool.standard_instruction(get_standard(EDITED_VERBATIM))
        for tag in ALLOWED_TAGS:
            self.assertIn(f'[{tag}]', text)

    def test_no_ellipsis_instruction_for_trailing_off(self):
        """
        Eagle's General Guidelines: "do not use ellipsis (...) in any
        job." This used to actively instruct the model to use one for a
        sentence that trails off -- the exact opposite.
        """
        for name in (EDITED_VERBATIM, STRICT_VERBATIM):
            text = warmpool.standard_instruction(get_standard(name))
            self.assertNotIn('Trailing off takes an ellipsis', text)
            self.assertIn('Never use an ellipsis', text)

    def test_the_vocalization_rule_points_the_right_way_for_each(self):
        """
        THE BRANCH THAT WAS SILENTLY BACKWARDS.

        The rule table reads (flag, when_on, when_off), and this one entry
        had its two strings swapped -- so edited verbatim was told to RECORD
        coughs and throat-clearing and strict verbatim was told not to, each
        the exact opposite of its own standard. Nothing caught it, because
        the opposites test above checked fillers and grammar and stopped
        there. It is asserted here in the direction the coach's rubric
        states, so a future swap fails instead of shipping.
        """
        edited = warmpool.standard_instruction(get_standard(EDITED_VERBATIM))
        strict = warmpool.standard_instruction(get_standard(STRICT_VERBATIM))

        self.assertIn('Omit non-essential vocalizations', edited)
        self.assertNotIn('Record non-speech sounds', edited)

        self.assertIn('Record non-speech sounds', strict)
        self.assertNotIn('Omit non-essential vocalizations', strict)

    def test_the_number_rule_never_travels_without_its_guard(self):
        """
        The style rule is what the coach requires; the guard is what makes
        it safe to ask for. Priming the model to convert numbers is what
        turned the variable X into "1.95E+3" and the word "knowledge" into
        "6E+1", so the prohibition must be present, and must come FIRST --
        the instruction is truncated from the end at 2000 characters, which
        means a rule that drifts past the limit is dropped in silence.
        """
        for name in (EDITED_VERBATIM, STRICT_VERBATIM):
            text = warmpool.standard_instruction(get_standard(name))
            guard = text.index('NEVER replace a word with a number')
            style = text.index('zero to nine stay WORDS')
            self.assertLess(guard, style,
                            f'{name}: the guard must precede the style rule')

    def test_only_strict_verbatim_standardizes_the_backchannels(self):
        # Edited verbatim removes uh-huh and mm-hmm as fillers, so spelling
        # them consistently is a rule that only means something in strict.
        self.assertIn('uh-huh',
                      warmpool.standard_instruction(get_standard(STRICT_VERBATIM)))
        self.assertNotIn('uh-huh',
                         warmpool.standard_instruction(get_standard(EDITED_VERBATIM)))

    def test_it_fits_the_custom_instruction_limit(self):
        # Over the limit the instruction is rejected at dispatch and the
        # whole standard pass silently becomes a no-op.
        from core.services.ai_assist.prompts import MAX_CUSTOM_INSTRUCTION_CHARS
        for name in (EDITED_VERBATIM, STRICT_VERBATIM):
            self.assertLess(
                len(warmpool.standard_instruction(get_standard(name))),
                MAX_CUSTOM_INSTRUCTION_CHARS,
            )


class ApplyStandardTestCase(TestCase):
    """
    Promoting the corrected text into the segments -- and the three
    conditions under which it must refuse to.
    """

    def setUp(self):
        super().setUp()
        project = Project.objects.create(name='standard-pass')
        self.job = TranscriptionJob.objects.create(
            project=project, input_source='upload', status='completed')
        self.segment = TranscriptSegment.objects.create(
            job=self.job, start_time=0.0, end_time=4.0,
            speaker_label='Male_1', text='Um, we was going, you know, there.')
        self.task = make_task(external_source='transcription',
                              external_task_id='5001', job=self.job)

    def run_with(self, processed, status='completed', quality_status='not_run'):
        """Stand in for the pipeline: create the revision rows it would."""
        def fake_pipeline(revision_id):
            revision = AIRevision.objects.get(pk=revision_id)
            AIRevisionSegment.objects.create(
                revision=revision, original_segment=self.segment,
                speaker_label=self.segment.speaker_label,
                start_time=self.segment.start_time,
                end_time=self.segment.end_time,
                original_text=self.segment.text,
                processed_text=processed,
                # The pipeline computes this by comparing the two texts;
                # effective_text returns the ORIGINAL when it is False, so a
                # stand-in that omits it yields a revision that changed
                # nothing.
                changed=(processed != self.segment.text),
            )
            AIRevision.objects.filter(pk=revision_id).update(
                status=status, quality_status=quality_status)

        with mock.patch('apps.transcriptions.tasks.run_ai_revision_pipeline',
                        side_effect=fake_pipeline):
            return warmpool.apply_transcription_standard(self.task, self.job)

    def test_the_corrected_text_becomes_what_the_worker_sees(self):
        applied = self.run_with('We were going there.')

        self.assertTrue(applied)
        self.segment.refresh_from_db()
        self.assertEqual(self.segment.text, 'We were going there.')

    def test_the_raw_asr_survives_for_audit(self):
        self.run_with('We were going there.')

        original = AIRevisionSegment.objects.get().original_text
        self.assertEqual(original, 'Um, we was going, you know, there.')

    def test_a_claimed_task_is_never_overwritten(self):
        """Warming is for the pool. Racing a worker's typing would destroy
        work they cannot get back."""
        _, ana = make_worker('ana', 'W-01')
        Task.objects.filter(pk=self.task.pk).update(claimed_by=ana)

        applied = self.run_with('We were going there.')

        self.assertFalse(applied)
        self.segment.refresh_from_db()
        self.assertEqual(self.segment.text, 'Um, we was going, you know, there.')

    def test_a_gate_refusal_is_not_promoted(self):
        """Text the gate would not sign off on is worse than raw ASR: it is
        confidently wrong, which is harder to review than obviously rough."""
        applied = self.run_with('We were going there.',
                                quality_status='quality_review_required')

        self.assertFalse(applied)
        self.segment.refresh_from_db()
        self.assertEqual(self.segment.text, 'Um, we was going, you know, there.')

    def test_an_unfinished_revision_is_not_promoted(self):
        applied = self.run_with('We were going there.', status='failed')

        self.assertFalse(applied)
        self.segment.refresh_from_db()
        self.assertEqual(self.segment.text, 'Um, we was going, you know, there.')


class WarmTranslationQualityProfileTestCase(TestCase):
    """
    `_warm_translation` must resolve `AI_QUALITY_DEFAULT_PROFILE` the exact
    same way `TranscriptionJobViewSet.ai_revisions` already does, rather
    than creating the revision ungated -- which is what it did before this
    was wired in, confirmed both in code and against 7 real days of
    production `AIUsageRecord` rows where every translate-operation row
    had exactly one request, zero exceptions.
    """

    def setUp(self):
        super().setUp()
        project = Project.objects.create(name='translation-gate')
        self.job = TranscriptionJob.objects.create(
            project=project, input_source='upload', status='completed')
        TranscriptSegment.objects.create(
            job=self.job, start_time=0.0, end_time=4.0,
            speaker_label='Male_1', text='Hello there.')
        self.task = make_task(
            kind=Task.TRANSLATION, external_source='translation',
            external_task_id='9001', job=self.job,
            source_language='en', target_language='fr',
        )

    def warm_and_capture_profile(self):
        """Runs _warm_translation with the pipeline faked, and returns the
        `quality_profile` the created AIRevision actually got -- read from
        the argument the (mocked) pipeline itself received, not guessed
        at from outside."""
        captured = {}

        def fake_pipeline(revision_id):
            revision = AIRevision.objects.get(pk=revision_id)
            captured['quality_profile'] = revision.quality_profile
            AIRevision.objects.filter(pk=revision_id).update(status='completed')

        with mock.patch('apps.transcriptions.tasks.run_ai_revision_pipeline',
                        side_effect=fake_pipeline):
            applied = warmpool._warm_translation(self.task, self.job)

        self.assertTrue(applied, 'the translation should complete under every profile here')
        return captured['quality_profile']

    def test_no_configured_default_leaves_it_ungated(self):
        """The pre-existing behaviour, still available: an operator who has
        not set AI_QUALITY_DEFAULT_PROFILE gets exactly what they had
        before this change -- one ungated request, no surprise spend."""
        with override_settings(AI_QUALITY_DEFAULT_PROFILE='none'):
            self.assertEqual(self.warm_and_capture_profile(), '')

    def test_auto_volume_gates_with_the_volume_profile(self):
        """The production setting this was written for."""
        with override_settings(AI_QUALITY_DEFAULT_PROFILE='auto:volume'):
            self.assertEqual(self.warm_and_capture_profile(), 'volume')

    def test_bare_auto_gates_with_the_translation_profile(self):
        """'auto' with no suffix defaults to the 'translation' profile --
        default_profile_for's own documented behaviour, exercised here
        rather than assumed."""
        with override_settings(AI_QUALITY_DEFAULT_PROFILE='auto'):
            self.assertEqual(self.warm_and_capture_profile(), 'translation')

    def test_an_explicit_named_profile_gates_every_operation_with_it(self):
        """The third form: naming a real profile directly (not 'auto:...')
        applies it unconditionally -- exercised so a future change to
        default_profile_for's precedence is caught here too."""
        with override_settings(AI_QUALITY_DEFAULT_PROFILE='eagle_web'):
            self.assertEqual(self.warm_and_capture_profile(), 'eagle_web')


class WarmMeansClaimableTestCase(TestCase):
    """
    THE DEADLOCK THIS PREVENTS.

    `is_warm` used to mean "a completed transcript is attached". For a
    translation that is only half the deliverable, and the half it misses is
    the one that matters:

      * a translation whose translate pass failed still had a transcript
      * so it counted as warm
      * so it filled a slot in the pool
      * so the pool reported itself full and warmed nothing further
      * so the failed translation was never retried

    Three half-finished tasks were enough to stop the warm pool completely,
    while every log line read `[warm] ready=3 processed=0` -- which looks
    like a healthy pool with nothing left to do.
    """

    def setUp(self):
        super().setUp()
        project = Project.objects.create(name='warm-meaning')
        self.job = TranscriptionJob.objects.create(
            project=project, input_source='upload', status='completed')

    def _task(self, kind):
        task = make_task(kind=kind, external_source='translation',
                         external_task_id=f'w-{kind}', status=states.NEW)
        Task.objects.filter(pk=task.pk).update(job=self.job)
        task.refresh_from_db()
        return task

    def test_a_transcription_is_warm_once_transcribed(self):
        self.assertTrue(warmpool.is_warm(self._task(Task.TRANSCRIPTION)))

    def test_a_translation_is_not_warm_on_the_transcript_alone(self):
        self.assertFalse(warmpool.is_warm(self._task(Task.TRANSLATION)))

    def test_a_translation_is_warm_once_translated(self):
        from apps.transcriptions.models import AIRevision

        task = self._task(Task.TRANSLATION)
        revision = AIRevision.objects.create(
            job=self.job, operation='translate', target_language='fr',
            status='completed')
        Task.objects.filter(pk=task.pk).update(deliverable_revision=revision)
        task.refresh_from_db()

        self.assertTrue(warmpool.is_warm(task))

    def test_a_polished_translation_counts_even_without_a_revision(self):
        """The worker's own version is the deliverable; it does not stop
        being one because the machine's attempt failed."""
        task = self._task(Task.TRANSLATION)
        Task.objects.filter(pk=task.pk).update(polished_text='Bonjour.')
        task.refresh_from_db()

        self.assertTrue(warmpool.is_warm(task))

    def test_a_failed_job_is_offered_for_warming_again(self):
        """
        THE SAME DEADLOCK, THROUGH A DIFFERENT DOOR.

        The half-done-translation fix readmitted a job that COMPLETED
        without producing a translation. It did not consider a job that
        FAILED -- and such a task is warm to nobody (the job is not
        completed) and a candidate for nothing (the job is not null), so it
        is stranded permanently.

        Not hypothetical: FFmpeg resolves through the installing user's
        AppData while the scheduled task runs as SYSTEM, so every pipeline
        failed for one afternoon and stranded 42 of 46 open tasks. Fixing
        FFmpeg alone would have left all 42 poisoned.
        """
        for kind in (Task.TRANSCRIPTION, Task.TRANSLATION):
            with self.subTest(kind=kind):
                job = TranscriptionJob.objects.create(
                    project=self.job.project, input_source='upload',
                    status='failed')
                task = make_task(kind=kind, external_source='translation',
                                 external_task_id=f'failed-{kind}',
                                 status=states.NEW)
                Task.objects.filter(pk=task.pk).update(job=job)
                task.refresh_from_db()

                self.assertFalse(warmpool.is_warm(task))
                self.assertIn(task, warmpool.select_candidates(20))

    def test_a_retry_never_outranks_work_that_has_not_been_tried(self):
        """
        There is no attempt counter, so a task that fails for a reason we
        cannot fix would come back every cycle and -- being short -- sort
        to the front and spend the whole pool re-failing on itself. Fresh
        work goes first; retries take what is left.
        """
        failed_job = TranscriptionJob.objects.create(
            project=self.job.project, input_source='upload', status='failed')
        short_retry = make_task(kind=Task.TRANSCRIPTION,
                                external_source='translation',
                                external_task_id='retry-short',
                                status=states.NEW, duration_seconds=10)
        Task.objects.filter(pk=short_retry.pk).update(job=failed_job)

        long_fresh = make_task(kind=Task.TRANSCRIPTION,
                               external_source='translation',
                               external_task_id='fresh-long',
                               status=states.NEW, duration_seconds=9000)

        picked = warmpool.select_candidates(20)
        self.assertLess(picked.index(long_fresh), picked.index(short_retry),
                        'a never-tried task must be warmed before a retry')

    def test_a_half_done_translation_is_offered_for_warming_again(self):
        """The whole point: it must come back as a candidate, or the failed
        translation is never retried."""
        task = self._task(Task.TRANSLATION)

        self.assertIn(task, warmpool.select_candidates(5))

    def test_a_transcription_missing_only_its_topic_is_offered_again(self):
        """
        A THIRD DOOR TO THE SAME DEADLOCK, smaller than the other two: a
        transcription task is fully warm and fully claimable with no topic
        at all (see test_a_transcription_is_warm_once_transcribed above),
        so this must NOT change is_warm -- but a topic lost to a transient
        provider failure (generate_topic is one attempt, fire-and-forget)
        used to have no way back in, because a completed transcription job
        read as fully done to select_candidates. Confirmed live: a run of
        Gemini 503s left real tasks permanently titled with their raw
        Eagle filename instead of a subject line.
        """
        task = self._task(Task.TRANSCRIPTION)
        self.assertEqual(task.topic, '')

        self.assertTrue(warmpool.is_warm(task), 'must still be claimable')
        self.assertIn(task, warmpool.select_candidates(5))

    def test_a_transcription_with_a_topic_is_not_offered_again(self):
        """The retry door must close once a topic actually lands, or every
        cycle re-spends a request on a task that no longer needs one."""
        task = self._task(Task.TRANSCRIPTION)
        Task.objects.filter(pk=task.pk).update(topic='A Real Subject Line')
        task.refresh_from_db()

        self.assertNotIn(task, warmpool.select_candidates(5))

    def test_warm_task_retries_only_the_topic_not_the_whole_transcript(self):
        """
        RESUME MEANS RESUME, NOT RESTART -- same principle as the
        translation branch right above this one in warm_task itself. The
        transcript already exists; re-fetching the audio or re-running the
        pipeline would waste real machine time reproducing work already
        sitting there, exactly the mistake that branch was written to
        avoid for translation.
        """
        task = self._task(Task.TRANSCRIPTION)

        with mock.patch.object(warmpool, 'generate_topic',
                               return_value='A Generated Subject') as gen, \
             mock.patch.object(warmpool, 'fetch_media') as fetch, \
             mock.patch('apps.transcriptions.tasks.run_transcription_pipeline') as pipeline:
            ok = warmpool.warm_task(task, service=None, allow_ai=True)

        self.assertTrue(ok)
        gen.assert_called_once_with(self.job)
        fetch.assert_not_called()
        pipeline.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.topic, 'A Generated Subject')

    def test_warm_task_leaves_the_topic_empty_when_generation_still_fails(self):
        """A second 503 must not be mistaken for progress, or the retry
        door (select_candidates) would close on a task that still needs
        one -- see generate_topic's own '' on any failure contract."""
        task = self._task(Task.TRANSCRIPTION)

        with mock.patch.object(warmpool, 'generate_topic', return_value=''):
            ok = warmpool.warm_task(task, service=None, allow_ai=True)

        self.assertTrue(ok, 'not a crash -- generate_topic already reports its own failure')
        task.refresh_from_db()
        self.assertEqual(task.topic, '')
        self.assertIn(task, warmpool.select_candidates(5))

    def test_warm_task_defers_the_topic_retry_without_ai_budget(self):
        task = self._task(Task.TRANSCRIPTION)

        with mock.patch.object(warmpool, 'generate_topic') as gen:
            ok = warmpool.warm_task(task, service=None, allow_ai=False)

        gen.assert_not_called()
        self.assertFalse(ok)


class StageSplitTestCase(TestCase):
    """
    Which half of the pipeline each task gets, and why it is rationed.

    THE TWO HALVES COST NOTHING ALIKE. Stage A -- fetch, ASR, diarization --
    is local, free and slow. Stage B is the standard pass or the translation:
    2-3 provider calls for a translation, 5-14 for a transcription, against a
    metered free tier that 84%% of one day's tokens had already gone to.

    THE CASE THAT FORCED IT. Task 4705 was warmed end to end -- a 20-minute
    file -- and another company's tasker took it 102 seconds after its French
    landed. The pool is shared and drains fast, so quota spent on a long file
    is the likeliest to be spent on work we never do.

    So Stage A runs on everything and Stage B goes to the shortest few.
    """

    def setUp(self):
        super().setUp()
        self.project = Project.objects.create(name='stage-split')

    def _task(self, external_id, seconds, kind=Task.TRANSCRIPTION, job_status=None):
        task = make_task(kind=kind, external_source='transcription',
                         external_task_id=external_id, status=states.NEW,
                         duration_seconds=seconds)
        if job_status:
            job = TranscriptionJob.objects.create(
                project=self.project, input_source='upload', status=job_status)
            Task.objects.filter(pk=task.pk).update(job=job)
            task.refresh_from_db()
        return task

    def _run(self, ai_limit):
        """Records what each task was scheduled with, warming nothing."""
        seen = {}

        def record(task, service, allow_ai=True):
            seen[task.external_task_id] = allow_ai
            return True, ''

        with mock.patch.object(warmpool, '_warm_guarded', side_effect=record):
            report = warmpool.run_cycle(pool_size=50, concurrency=1,
                                        ai_limit=ai_limit)
        return seen, report

    def test_the_shortest_tasks_get_the_expensive_half(self):
        self._task('short', 120)
        self._task('medium', 600)
        self._task('long', 1200)

        seen, report = self._run(ai_limit=1)

        # Every task still gets Stage A -- that is the free, slow half.
        self.assertEqual(set(seen), {'short', 'medium', 'long'})
        # Only the shortest spends quota.
        self.assertTrue(seen['short'])
        self.assertFalse(seen['medium'])
        self.assertFalse(seen['long'])
        self.assertEqual(report.stage_a_only, 2)

    def test_with_budget_for_everything_nothing_is_held_back(self):
        self._task('a', 100)
        self._task('b', 200)

        seen, report = self._run(ai_limit=10)

        self.assertTrue(all(seen.values()))
        self.assertEqual(report.stage_a_only, 0)

    def test_a_translation_awaiting_only_its_translation_is_not_given_a_pointless_slot(self):
        """
        Stage A cannot advance it -- the transcript is already banked, and
        only the provider can produce the French. Out of budget it waits for
        a cycle with room instead of occupying a slot to do nothing.
        """
        self._task('4705', 1200, kind=Task.TRANSLATION, job_status='completed')

        seen, report = self._run(ai_limit=0)

        self.assertEqual(seen, {})
        self.assertEqual(report.deferred, 1)
        self.assertEqual(report.processed, 0)

    def test_that_same_translation_is_taken_up_once_there_is_budget(self):
        self._task('4705', 1200, kind=Task.TRANSLATION, job_status='completed')

        seen, report = self._run(ai_limit=1)

        self.assertEqual(seen, {'4705': True})
        self.assertEqual(report.deferred, 0)

    def test_a_fresh_translation_still_gets_its_transcript_without_budget(self):
        """Stage A has real work to do here, so the slot is worth taking:
        the ASR is banked and the next cycle translates without redoing it."""
        self._task('4699', 1200, kind=Task.TRANSLATION)

        seen, report = self._run(ai_limit=0)

        self.assertEqual(seen, {'4699': False})
        self.assertEqual(report.stage_a_only, 1)
