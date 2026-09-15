"""
Submitting to Eagle.

THE ONE THAT MATTERS MOST is the ordering test. A task marked SUBMITTED that
never reached the platform looks like income that will never arrive, and
nobody goes looking for it because the record says it is fine. Every failure
path here therefore asserts our state did NOT move.

The gate tests are about money in the other direction: a rejection costs the
purchased minutes plus the worker's hour, so the cheap deterministic checks
have to catch a broken document before it is spent.
"""
import itertools
from unittest import mock

from django.utils import timezone
from django.test import TestCase, override_settings

from apps.transcriptions.models import (
    AIQualityReport, AIRevision, AIRevisionSegment, Project, TranscriptionJob,
    TranscriptSegment,
)

from . import eagle_submit, services, states
from .eagle import (
    EagleAuthError, EagleIntegrationError, EagleIntegrationService,
    EagleSubmissionResult, EagleTask,
)
from .eagle_submit import (
    SubmissionFailed, SubmissionRefused, preview_submission, render_for_eagle,
    run_quality_gate, submit_task,
)
from .models import Task, TaskEvent
from .tests import make_task, make_worker


class FakeEagle(EagleIntegrationService):
    def __init__(self, accepted=True, error=None):
        self.accepted = accepted
        self.error = error
        self.submissions = []

    def is_configured(self):
        return True

    def submit_task(self, external_id, content, source='', metadata=None):
        if self.error:
            raise self.error
        self.submissions.append((source, external_id, content))
        return EagleSubmissionResult(
            accepted=self.accepted, reference=str(external_id),
            message='ok' if self.accepted else 'refused',
        )


#: ONE SENTENCE DELIBERATELY CARRIES A CONTRACTION ("weren't"). Every other
#: standard/policy flag under edited-verbatim is preserve-only now (see
#: TranscriptionStandard's own docstring), and check_standard_policy warns
#: MEDIUM when a transcript of this length has NONE at all -- exactly what
#: this fixture, otherwise all formal third-person narration, would
#: trigger by accident. Real speech almost always has at least one; this
#: keeps the fixture realistic rather than suppressing a real check.
_TOPICS = [
    'The committee reviewed the proposal at length and reached agreement.',
    'A second witness described the sequence of events that morning.',
    'The report sets out the methodology used throughout the study.',
    'Several participants raised concerns about the proposed timetable.',
    'The findings were circulated to every department before publication.',
    'A final section summarises the recommendations for the coming year.',
    'The chair opened by thanking everyone for attending on short notice.',
    'Funding arrangements were discussed without reaching a firm conclusion.',
    'An appendix lists every source consulted during the review period.',
    'The working group will reconvene once the consultation has closed.',
    "Members asked for clearer guidance because they weren't sure how "
    'objections are handled.',
    'The minutes were approved and the meeting closed shortly afterwards.'
]
#: TYPOGRAPHIC APOSTROPHES (') THROUGHOUT, NOT STRAIGHT ONES (') --
#: deliberate, since check_typography's fr.apostrophe rule flags the
#: straight form and this is supposed to be a "good document" fixture.
#: A hand-typed straight apostrophe here is exactly the kind of
#: unrealistic-fixture gap that rule exists to catch on a real
#: submission -- see typography.py's own docstring for the evidence.
_TOPICS_FR = [
    'Le comité a examiné la proposition en détail et est parvenu à un accord.',
    'Un deuxième témoin a décrit la suite des événements ce matin-là.',
    'Le rapport expose la méthodologie utilisée tout au long de l’étude.',
    'Plusieurs participants ont exprimé des inquiétudes sur le calendrier.',
    'Les conclusions ont été diffusées à chaque service avant publication.',
    'Une dernière section résume les recommandations pour l’année à venir.',
    'Le président a remercié chacun d’être venu dans un délai aussi court.',
    'Les modalités de financement ont été évoquées sans conclusion ferme.',
    'Une annexe recense chaque source consultée pendant la période d’examen.',
    'Le groupe de travail se réunira de nouveau à la clôture de la consultation.',
    'Les membres ont demandé des orientations plus claires sur les objections.',
    'Le procès-verbal a été approuvé et la séance levée peu après.'
]


#: Unique usernames for the self-check worker `build_task` invents when the
#: caller does not supply one, so two calls in the same test never collide
#: on User's unique username.
_checker_seq = itertools.count(1)


def build_task(kind=Task.TRANSCRIPTION, seconds=600, segments=12, worker=None,
               self_checked=True):
    """
    self_checked=True MEANS THIS IS A NORMAL, SUBMITTABLE FIXTURE.

    run_quality_gate now refuses any task where the worker's own
    self-check step never ran (see 'self-check-not-completed'), the same
    way it already refused an untranslated or timestamp-less document.
    Every existing caller of this fixture wants "a good task", so that is
    the default; a test specifically about the new gate passes
    self_checked=False instead of tearing the field back out afterwards.
    """
    project = Project.objects.create(name='Eagle')
    job = TranscriptionJob.objects.create(
        project=project, input_source='upload', status='completed',
        enable_diarization=(kind == Task.TRANSCRIPTION),
    )
    for i in range(segments):
        TranscriptSegment.objects.create(
            job=job, start_time=i * 50.0, end_time=(i + 1) * 50.0,
            speaker_label='Male_1' if i % 2 == 0 else 'Male_2',
            # Long enough that the "suspiciously short" floor is cleared,
            # CAPITALIZED (a speaker block starting in lower case is a real
            # defect under edited verbatim), and DIFFERENT PER SEGMENT.
            #
            # The last of those matters: a fixture that repeats one
            # identical sentence twelve times is a duplicated paragraph, and
            # the duplication check is right to say so. A "good document"
            # fixture has to actually be one.
            #
            # NO DIGITS. The number check compares figures across the two
            # languages, so a counter baked into the source text has to
            # appear in the translation too -- which makes the fixture about
            # the fixture rather than about what is being tested.
            text=((_TOPICS[i % len(_TOPICS)] + ' ') * 4).strip(),
        )
    task = make_task(
        kind=kind, external_source='transcription' if kind == Task.TRANSCRIPTION else 'translation',
        external_task_id='4333', duration_seconds=seconds,
        status=states.READY_TO_SUBMIT, job=job, claimed_by=worker,
        title='transcription job',
    )

    if kind == Task.TRANSLATION:
        # WITHOUT THIS THE TASK CANNOT LEGALLY RENDER. render_for_eagle
        # refuses a translation task with no translated revision, because
        # the only thing it could otherwise send is the English source
        # under a "Target Language" heading -- a certain rejection.
        revision = AIRevision.objects.create(
            job=job, operation='translate', label='Translation (fr)',
            source_language='en', target_language='fr', status='completed',
        )
        for segment in job.segments.all().order_by('start_time'):
            AIRevisionSegment.objects.create(
                revision=revision, original_segment=segment,
                speaker_label=segment.speaker_label,
                start_time=segment.start_time, end_time=segment.end_time,
                original_text=segment.text,
                # Varied for the same reason as the source above.
                processed_text=(
                    (_TOPICS_FR[int(segment.start_time // 50) % len(_TOPICS_FR)]
                     + ' ') * 4).strip(),
                changed=True,
            )
        Task.objects.filter(pk=task.pk).update(deliverable_revision=revision)
        task.refresh_from_db()

    if self_checked:
        checker = worker
        if checker is None:
            n = next(_checker_seq)
            checker = make_worker(f'checker{n}', f'CHK-{n:03d}')[1]
        if kind == Task.TRANSCRIPTION:
            Task.objects.filter(pk=task.pk).update(
                accuracy_check_notes='Checked against the audio; nothing to report.',
                accuracy_check_by=checker, accuracy_check_at=timezone.now(),
            )
        else:
            Task.objects.filter(pk=task.pk).update(
                polished_by=checker, polished_at=timezone.now(),
            )
        task.refresh_from_db()

    return task


class RenderingTestCase(TestCase):

    def test_transcription_renders_in_the_accepted_eagle_shape(self):
        content = render_for_eagle(build_task(Task.TRANSCRIPTION))

        # Validated against a submission Eagle actually accepted.
        self.assertIn('Transcription Job:', content)
        self.assertIn('Client: Eagle Web', content)
        self.assertIn('Audio Length:', content)
        self.assertIn('-->', content)
        self.assertIn('[END OF RECORDING', content)

    def test_translation_renders_without_timestamps_and_with_a_word_count(self):
        content = render_for_eagle(build_task(Task.TRANSLATION))

        self.assertIn('Translation Job:', content)
        self.assertIn('Translated By:', content)
        self.assertIn('Translation :', content)
        self.assertIn('Number Of Words:', content)
        self.assertNotIn('-->', content)

    def test_a_task_with_no_transcript_is_refused_before_anything_else(self):
        task = make_task(external_task_id='1', status=states.READY_TO_SUBMIT)

        with self.assertRaises(SubmissionRefused):
            render_for_eagle(task)


class QualityGateTestCase(TestCase):

    def test_an_empty_document_is_critical(self):
        findings = run_quality_gate(build_task(), '')
        self.assertEqual(findings[0]['severity'], 'critical')

    def test_a_transcript_without_timestamps_is_critical(self):
        # Eagle grades timing; submitting without it is a certain rejection.
        findings = run_quality_gate(build_task(), 'Male_1:\nsome words ' * 200)
        codes = [f['code'] for f in findings]
        self.assertIn('missing-timestamps', codes)

    def test_a_suspiciously_short_transcript_is_caught(self):
        """The signature of a truncated pipeline run, and it costs minutes."""
        task = build_task(seconds=1500)
        findings = run_quality_gate(task, 'Male_1:\n[00:00:00.000 --> 00:00:01.000]\nhello')

        self.assertIn('suspiciously-short', [f['code'] for f in findings])

    def test_a_good_transcription_document_produces_no_findings(self):
        task = build_task(Task.TRANSCRIPTION, seconds=600)
        self.assertEqual(run_quality_gate(task, render_for_eagle(task)), [])

    def test_a_good_translation_document_produces_no_findings(self):
        task = build_task(Task.TRANSLATION, seconds=600)
        self.assertEqual(run_quality_gate(task, render_for_eagle(task)), [])

    def test_the_preview_is_the_exact_bytes_that_would_be_sent(self):
        task = build_task()
        preview = preview_submission(task)
        self.assertEqual(preview.content, render_for_eagle(task))

    def test_a_transcription_task_is_blocked_without_the_self_check(self):
        """
        THE NEW GATE. A worker's own assistant, reading the actual
        recording's subject matter, catches vocabulary and domain
        defects no hand-maintained rule was ever written for -- and that
        only works if the step actually ran. See
        TranscriptCheckPromptView's own docstring.
        """
        task = build_task(Task.TRANSCRIPTION, seconds=600, self_checked=False)
        findings = run_quality_gate(task, render_for_eagle(task))
        codes = {f['code'] for f in findings}
        self.assertIn('self-check-not-completed', codes)
        self.assertEqual(
            next(f for f in findings
                 if f['code'] == 'self-check-not-completed')['severity'],
            'critical',
        )

    def test_a_translation_task_is_blocked_without_the_self_check(self):
        task = build_task(Task.TRANSLATION, seconds=600, self_checked=False)
        findings = run_quality_gate(task, render_for_eagle(task))
        codes = {f['code'] for f in findings}
        self.assertIn('self-check-not-completed', codes)

    def test_completing_the_self_check_clears_the_finding(self):
        """The fix must not blind the check to a real one: a task WITH the
        step completed produces no self-check finding -- covered already
        by the "good document" tests above via self_checked's default, but
        asserted directly here against the specific code."""
        task = build_task(Task.TRANSCRIPTION, seconds=600, self_checked=True)
        findings = run_quality_gate(task, render_for_eagle(task))
        codes = {f['code'] for f in findings}
        self.assertNotIn('self-check-not-completed', codes)


class SubmissionOrderingTestCase(TestCase):
    """Eagle first; our state only on acknowledgement."""

    def setUp(self):
        self.user, self.worker = make_worker('ana', 'W-01')

    def test_a_successful_submission_calls_eagle_then_records_it(self):
        task = build_task(worker=self.worker)
        fake = FakeEagle()

        submitted = submit_task(task.id, self.user, service=fake)

        self.assertEqual(len(fake.submissions), 1)
        self.assertEqual(submitted.status, states.SUBMITTED)

    def test_what_is_sent_is_what_the_renderer_produced(self):
        task = build_task(worker=self.worker)
        fake = FakeEagle()

        submit_task(task.id, self.user, service=fake)

        _, _, sent = fake.submissions[0]
        self.assertIn('[END OF RECORDING', sent)
        self.assertEqual(sent, render_for_eagle(Task.objects.get(pk=task.id)))

    def test_our_state_does_not_move_when_the_platform_refuses(self):
        task = build_task(worker=self.worker)

        with self.assertRaises(SubmissionFailed):
            submit_task(task.id, self.user, service=FakeEagle(accepted=False))

        task.refresh_from_db()
        self.assertEqual(task.status, states.READY_TO_SUBMIT)
        self.assertIsNone(task.submitted_at)

    def test_our_state_does_not_move_when_the_platform_is_unreachable(self):
        task = build_task(worker=self.worker)
        fake = FakeEagle(error=EagleIntegrationError('connection reset'))

        with self.assertRaises(SubmissionFailed):
            submit_task(task.id, self.user, service=fake)

        task.refresh_from_db()
        self.assertEqual(task.status, states.READY_TO_SUBMIT)

    def test_a_failure_is_recorded_loudly_rather_than_looking_like_success(self):
        task = build_task(worker=self.worker)

        with self.assertRaises(SubmissionFailed):
            submit_task(task.id, self.user, service=FakeEagle(accepted=False))

        event = TaskEvent.objects.filter(task=task, success=False).first()
        self.assertIsNotNone(event, 'a failed submission left no trace')

    def test_an_expired_session_refuses_without_moving_state(self):
        task = build_task(worker=self.worker)
        fake = FakeEagle(error=EagleAuthError('session rejected'))

        with self.assertRaises(SubmissionRefused):
            submit_task(task.id, self.user, service=fake)

        task.refresh_from_db()
        self.assertEqual(task.status, states.READY_TO_SUBMIT)
        self.assertEqual(fake.submissions, [])


class TransportErrorConfirmationTestCase(TestCase):
    """
    A transport error is not proof the write did not land.

    THIS HAPPENED. Task 4639 was POSTed to Eagle, Eagle recorded it -- status
    `submitted`, a `submitted_at`, our document in `translated_text` -- and
    the response came back unreadable. The worker was told "the platform
    could not be reached, try again", which invites a SECOND submission of
    work already delivered and leaves our state diverged from the platform's
    until somebody reconciles it by hand.
    """

    def setUp(self):
        self.user, self.worker = make_worker('ana', 'W-01')

    def _service(self, external_status):
        """A service whose submit dies but whose read answers."""
        class Confirming(FakeEagle):
            def get_task(inner, external_id, source=''):
                return EagleTask(
                    external_id=str(external_id), source=source,
                    raw={'external_status': external_status,
                         'mapped_status': external_status},
                )
        return Confirming(error=EagleIntegrationError('connection reset'))

    def test_a_submission_the_platform_confirms_is_treated_as_delivered(self):
        task = build_task(worker=self.worker)

        submitted = submit_task(task.id, self.user, service=self._service('submitted'))

        self.assertEqual(submitted.status, states.SUBMITTED)
        self.assertIsNotNone(submitted.submitted_at)

    def test_a_submission_the_platform_has_not_seen_still_fails(self):
        # The safe direction. An unnecessary retry costs a minute; believing
        # a lost submission landed costs the whole task, silently, until the
        # deadline passes.
        task = build_task(worker=self.worker)

        with self.assertRaises(SubmissionFailed):
            submit_task(task.id, self.user, service=self._service('available'))

        task.refresh_from_db()
        self.assertNotEqual(task.status, states.SUBMITTED)

    def test_a_confirming_read_that_also_fails_is_reported_as_failure(self):
        # We are no wiser than before, so we must not claim success.
        class Blind(FakeEagle):
            def get_task(inner, external_id, source=''):
                raise EagleIntegrationError('also unreachable')

        task = build_task(worker=self.worker)

        with self.assertRaises(SubmissionFailed):
            submit_task(task.id, self.user,
                        service=Blind(error=EagleIntegrationError('connection reset')))

        task.refresh_from_db()
        self.assertNotEqual(task.status, states.SUBMITTED)


class SubmitFromEarlierStatesTestCase(TestCase):
    """
    Submit means submit, from wherever the worker is standing.

    THIS BIT TWICE, ON REAL MONEY. `submit_task` accepts a task in
    IN_PROGRESS, but the only transition into SUBMITTED is from
    READY_TO_SUBMIT -- so pressing Submit from IN_PROGRESS rendered the
    document, passed the gate, POSTed it to Eagle, and only THEN failed with
    "a task cannot move from in_progress to submitted with your
    permissions." Eagle had the work; we refused to record it; the two
    disagreed until somebody reconciled by hand.

    The walk to READY_TO_SUBMIT now happens BEFORE the external call, so a
    task that cannot get there is refused while refusing is still free.
    """

    def setUp(self):
        self.user, self.worker = make_worker('ana', 'W-01')

    def _at(self, status):
        task = build_task(worker=self.worker)
        Task.objects.filter(pk=task.pk).update(status=status)
        task.refresh_from_db()
        return task

    def test_submitting_from_in_progress_reaches_the_platform_and_is_recorded(self):
        task = self._at(states.IN_PROGRESS)
        fake = FakeEagle()

        submitted = submit_task(task.id, self.user, service=fake)

        self.assertEqual(len(fake.submissions), 1)
        self.assertEqual(submitted.status, states.SUBMITTED)
        self.assertIsNotNone(submitted.submitted_at)

    def test_submitting_from_review_reaches_the_platform_and_is_recorded(self):
        task = self._at(states.REVIEW)
        fake = FakeEagle()

        submitted = submit_task(task.id, self.user, service=fake)

        self.assertEqual(len(fake.submissions), 1)
        self.assertEqual(submitted.status, states.SUBMITTED)

    def test_a_task_that_cannot_reach_ready_is_refused_before_any_request(self):
        # The refusal has to happen while it is still free. A task that
        # cannot legally reach SUBMITTED must never cost a platform call.
        task = self._at(states.NEW)
        fake = FakeEagle()

        with self.assertRaises((SubmissionRefused, SubmissionFailed)):
            submit_task(task.id, self.user, service=fake)

        self.assertEqual(len(fake.submissions), 0)


class GateBlockingTestCase(TestCase):

    def setUp(self):
        self.user, self.worker = make_worker('ana', 'W-01')

    def test_a_critical_finding_blocks_before_the_platform_is_called(self):
        task = build_task(worker=self.worker, seconds=1500, segments=1)
        fake = FakeEagle()

        with self.assertRaises(SubmissionRefused):
            submit_task(task.id, self.user, service=fake)

        # The point: nothing was spent and nothing was sent.
        self.assertEqual(fake.submissions, [])

    def test_force_does_not_override_a_critical_finding(self):
        """
        The T-2 endgame is a gamble on a mediocre document, not on a broken
        one. Submitting an empty transcript is not a gamble -- it is a
        certain rejection plus a mark against the account.
        """
        task = build_task(worker=self.worker, seconds=1500, segments=1)
        fake = FakeEagle()

        with self.assertRaises(SubmissionRefused):
            submit_task(task.id, self.user, service=fake, force=True)

        self.assertEqual(fake.submissions, [])

    def test_force_does_send_when_findings_are_only_advisory(self):
        task = build_task(worker=self.worker)
        fake = FakeEagle()

        with mock.patch('apps.workforce.eagle_submit.run_quality_gate',
                        return_value=[{'severity': 'high', 'code': 'x',
                                       'message': 'worth a look'}]):
            submitted = submit_task(task.id, self.user, service=fake, force=True)

        self.assertEqual(submitted.status, states.SUBMITTED)
        self.assertEqual(len(fake.submissions), 1)

    def test_a_forced_submission_is_marked_as_forced_in_the_record(self):
        task = build_task(worker=self.worker)
        fake = FakeEagle()

        with mock.patch('apps.workforce.eagle_submit.run_quality_gate', return_value=[]):
            submit_task(task.id, self.user, service=fake, force=True,
                        reason='Auto-submitted at T-2 minutes.')

        event = TaskEvent.objects.filter(
            task=task, event_type=TaskEvent.SUBMISSION_COMPLETED).first()
        self.assertTrue(event.metadata['forced'])
        self.assertIn('T-2', event.message)

    def test_a_task_in_the_wrong_state_is_refused(self):
        task = build_task(worker=self.worker)
        Task.objects.filter(pk=task.pk).update(status=states.SUBMITTED)
        fake = FakeEagle()

        with self.assertRaises(SubmissionRefused):
            submit_task(task.id, self.user, service=fake)

        self.assertEqual(fake.submissions, [])


class CoachStandardGateTestCase(TestCase):
    """
    THE COACH'S RULES, RUN AGAINST THE DOCUMENT WE ARE ABOUT TO SEND.

    These checks were written from Eagle's guide and were, until now,
    unreachable code -- nothing called run_transcript_checks. Each one
    corresponds to something the platform actually returns work for, and
    each costs zero provider requests, so there is no reason for a
    submission to leave without them having run.
    """

    def gate(self, content, kind=Task.TRANSCRIPTION, project=''):
        return eagle_submit.run_quality_gate(
            Task(kind=kind, duration_seconds=None, project=project), content)

    def codes(self, content, **kw):
        return {f['code'] for f in self.gate(content, **kw)}

    def test_a_speaker_name_is_refused(self):
        """The single most common rejection: Eagle permits Male_N/Female_N
        and nothing else -- no names, titles or court roles."""
        codes = self.codes(
            'John Smith:\n[00:00:01.000 --> 00:00:04.000]\nHello there.\n'
            '[END OF RECORDING --> 00:00:04.000]'
        )
        self.assertIn('transcript.speaker-label-format', codes)

    def test_a_correct_document_produces_no_standard_findings(self):
        clean = (
            'Male_1:\n[00:00:01.000 --> 00:00:04.000]\n'
            'Hello there, this is the first turn.\n\n'
            '[END OF RECORDING --> 00:00:04.000]'
        )
        self.assertEqual(
            [f for f in self.gate(clean)
             if f['code'].startswith('transcript.')], [],
        )

    def test_fillers_are_a_finding_under_edited_verbatim(self):
        with override_settings(EAGLE_TRANSCRIPTION_STANDARD='edited_verbatim'):
            codes = self.codes(
                'Male_1:\n[00:00:01.000 --> 00:00:04.000]\n'
                'Um, I was, you know, going there.\n'
                '[END OF RECORDING --> 00:00:04.000]'
            )
        self.assertIn('transcript.fillers-not-removed', codes)

    def test_the_same_fillers_are_correct_under_strict_verbatim(self):
        """
        A transcript that is flawless under one standard is a guaranteed
        rejection under the other. Proving the polarity actually inverts is
        the whole reason the standard is a setting rather than a constant.
        """
        with override_settings(EAGLE_TRANSCRIPTION_STANDARD='strict_verbatim'):
            codes = self.codes(
                'Male_1:\n[00:00:01.000 --> 00:00:04.000]\n'
                'Um, I was, you know, going there.\n'
                '[END OF RECORDING --> 00:00:04.000]'
            )
        self.assertNotIn('transcript.fillers-not-removed', codes)

    def test_the_project_decides_the_standard_not_the_global_setting(self):
        """
        A REAL BUG, CONFIRMED ON A REAL STRICT-VERBATIM (LEGAL) TASK.
        transcription_standard_for used to ignore `task` entirely and
        always return the global default -- so a legal-dictation task,
        whose standard pass correctly preserved every filler and whose
        on-screen banner correctly said "Strict verbatim", still had its
        Preview gate tell a worker to strip fillers a legal transcript is
        required to keep. The global setting is only ever the FALLBACK
        for a task with no project label -- see standard_for_project --
        so a project that resolves to strict verbatim must win even
        while the platform-wide default stays edited verbatim.
        """
        codes = self.codes(
            'Male_1:\n[00:00:01.000 --> 00:00:04.000]\n'
            'Um, I was, you know, going there.\n'
            '[END OF RECORDING --> 00:00:04.000]',
            project='legal dictation',
        )
        self.assertNotIn('transcript.fillers-not-removed', codes)

    def test_the_checks_do_not_run_on_a_translation(self):
        """A French translation has no Male_1: labels and no timestamp
        chain; running transcript rules over it would produce nothing but
        noise and block every translation submission."""
        codes = self.codes(
            'Translation :\nBonjour, ceci est la premiere phrase.\n'
            '(Number Of Words: 6)',
            kind=Task.TRANSLATION,
        )
        self.assertFalse({c for c in codes if c.startswith('transcript.')})

    def test_a_broken_checker_cannot_block_a_submission(self):
        """Finished work must not become unsubmittable because a check
        raised. The gate degrades to its own findings."""
        with mock.patch(
            'core.services.ai_assist.quality.run_transcript_checks',
            side_effect=RuntimeError('boom'),
        ):
            findings = self.gate(
                'Male_1:\n[00:00:01.000 --> 00:00:04.000]\nHello.\n'
                '[END OF RECORDING --> 00:00:04.000]'
            )
        self.assertFalse([f for f in findings
                          if f['code'].startswith('transcript.')])


class FindingLocationTestCase(TestCase):
    """
    Pointing a worker at the line, in words they can act on.

    THE CHECKS RUN OVER THE WHOLE DOCUMENT joined into one string, so a
    finding could name the offending value -- "16, 2" -- without saying
    which of 67 segments it lived in. The worker was handed a rule
    ("restore every quantity exactly as the source states it") and left to
    search for the place it applied.

    AND ON A TRANSLATION THE WORKER OFTEN CANNOT READ THE TARGET LANGUAGE.
    That is the normal case, not the exception. A message about a quantity
    being wrong is only actionable if it shows the English line, the French
    line, and which token differs.

    The substring test below is the important one: `"2" in "$62"` is true,
    and a location that confidently points at a CORRECT line is worse than
    no location at all.
    """

    def setUp(self):
        super().setUp()
        project = Project.objects.create(name='locating')
        self.job = TranscriptionJob.objects.create(
            project=project, input_source='upload', status='completed')
        self.task = make_task(kind=Task.TRANSLATION, external_source='translation',
                              external_task_id='4813', status=states.NEW,
                              target_language='fr')
        self.revision = AIRevision.objects.create(
            job=self.job, operation='translate', target_language='fr',
            status='completed')
        Task.objects.filter(pk=self.task.pk).update(
            job=self.job, deliverable_revision=self.revision)
        self.task.refresh_from_db()

    def _segment(self, start, english, french):
        source = TranscriptSegment.objects.create(
            job=self.job, start_time=start, end_time=start + 4, text=english)
        return AIRevisionSegment.objects.create(
            revision=self.revision, original_segment=source,
            start_time=start, end_time=start + 4,
            original_text=english, processed_text=french,
            changed=True, accepted=True)

    def _issue(self, source_span='', target_span=''):
        from core.services.ai_assist.quality.issues import (
            IssueCategory, QualityIssue, Severity,
        )
        return QualityIssue(
            category=IssueCategory.NUMERIC, severity=Severity.CRITICAL,
            problem='numbers differ', source_span=source_span,
            target_span=target_span, rule='numeric.missing-from-target',
        )

    def test_it_finds_the_line_the_number_actually_lives_on(self):
        self._segment(10, "he pays tax of $62", "il paie 62 $ d'impot")
        self._segment(173, 'a tax of 16 and 2 thirds percent',
                      'un impot de 16,67 pour cent')

        found = eagle_submit._locate(self._issue(source_span='16, 2'), self.task)

        self.assertEqual(found['segment_index'], 1)
        self.assertEqual(found['at'], '02:53')
        self.assertIn('16 and 2 thirds', found['source_text'])

    def test_it_does_not_match_a_number_inside_a_bigger_one(self):
        """`"2" in "$62"` is True, and that lands the worker on a line that
        is perfectly correct. Matching is on parsed values, not substrings."""
        self._segment(10, 'he pays tax of $62', 'il paie 62 $')

        found = eagle_submit._locate(self._issue(source_span='2'), self.task)

        self.assertEqual(found, {}, 'must not point at the $62 line')

    def test_it_matches_across_the_decimal_separator(self):
        """The checker reports 16.67; the French writes 16,67. A literal
        comparison finds nothing and the finding loses its location."""
        self._segment(173, 'a tax of 16 and 2 thirds percent',
                      'un impot de 16,67 pour cent')

        found = eagle_submit._locate(self._issue(target_span='16.67'), self.task)

        self.assertEqual(found['segment_index'], 0)
        self.assertIn('invented', found['action'])

    def test_the_action_names_both_sides(self):
        """The worker who most needs this cannot read the French, so the
        message must say what each side uses rather than only what is
        missing -- "16 is missing" reads as nonsense beside "16,67"."""
        self._segment(173, 'a tax of 16 and 2 thirds percent',
                      'un impot de 16,67 pour cent')

        action = eagle_submit._locate(self._issue(source_span='16, 2'),
                                      self.task)['action']

        self.assertIn('the English uses', action)
        self.assertIn('the French uses', action)
        self.assertIn('16.67', action)

    def test_an_unlocatable_finding_keeps_its_document_level_message(self):
        self._segment(10, 'no numbers here at all', 'aucun chiffre ici')

        self.assertEqual(eagle_submit._locate(self._issue(source_span='99'),
                                              self.task), {})

    def test_a_finding_with_no_span_is_left_alone(self):
        self._segment(10, 'a tax of 16 percent', 'un impot de 16 pour cent')

        self.assertEqual(eagle_submit._locate(self._issue(), self.task), {})

    def test_it_quotes_the_polished_version_the_worker_actually_submits(self):
        """
        THE POLISHED VERSION IS THE DOCUMENT once a worker has pasted one --
        `_render` sends it and the checks run against it. Quoting the
        machine draft beside a finding computed from the polished text would
        show somebody French that appears nowhere on their screen.
        """
        self._segment(173, 'a tax of 16 and 2 thirds percent',
                      'MACHINE: un impot de 16,67 pour cent')
        Task.objects.filter(pk=self.task.pk).update(
            polished_text='POLISHED: un impot de 16,67 pour cent')
        self.task.refresh_from_db()

        found = eagle_submit._locate(self._issue(target_span='16.67'), self.task)

        self.assertIn('POLISHED', found['target_text'])
        self.assertNotIn('MACHINE', found['target_text'])

    def test_when_the_paragraphs_do_not_line_up_it_quotes_no_french_at_all(self):
        """A wrong quote is worse than none. The English stays, because the
        source is authoritative however the target was rewritten."""
        self._segment(10, 'first line with 16 and 2 thirds percent', 'machine one')
        self._segment(20, 'second line', 'machine two')
        Task.objects.filter(pk=self.task.pk).update(
            polished_text='a single polished paragraph that replaced both')
        self.task.refresh_from_db()

        found = eagle_submit._locate(self._issue(source_span='16, 2'), self.task)

        self.assertIn('first line', found['source_text'])
        self.assertEqual(found['target_text'], '')
        self.assertIn('missing from your translation', found['action'])


class LocateSemanticTestCase(TestCase):
    """
    `_locate_semantic` looks a segment up by the index the audit already
    attached, rather than searching for it -- unlike `_locate` above, which
    has to search because deterministic checks run over the whole document
    joined into one string and never know which segment produced a value.
    A completeness or register finding usually names no number at all, so
    `_locate`'s search would never find it; a direct index lookup does not
    care.
    """

    def setUp(self):
        super().setUp()
        project = Project.objects.create(name='locate-semantic')
        self.job = TranscriptionJob.objects.create(
            project=project, input_source='upload', status='completed')
        self.task = make_task(kind=Task.TRANSLATION, external_source='translation',
                              external_task_id='5002', status=states.NEW,
                              target_language='fr')
        self.revision = AIRevision.objects.create(
            job=self.job, operation='translate', target_language='fr',
            status='completed')
        Task.objects.filter(pk=self.task.pk).update(
            job=self.job, deliverable_revision=self.revision)
        self.task.refresh_from_db()

    def _segment(self, start, english, french):
        source = TranscriptSegment.objects.create(
            job=self.job, start_time=start, end_time=start + 4, text=english)
        return AIRevisionSegment.objects.create(
            revision=self.revision, original_segment=source,
            start_time=start, end_time=start + 4,
            original_text=english, processed_text=french,
            changed=True, accepted=True)

    def _issue(self, segment_index=None):
        from core.services.ai_assist.quality.issues import (
            IssueCategory, QualityIssue, Severity,
        )
        return QualityIssue(
            category=IssueCategory.COMPLETENESS, severity=Severity.CRITICAL,
            problem='a non-numeric completeness defect, on purpose',
            segment_index=segment_index,
        )

    def test_it_finds_the_segment_by_index_with_no_numbers_involved(self):
        self._segment(10, 'first english line', 'premiere ligne francaise')
        self._segment(173, 'second english line', 'deuxieme ligne francaise')

        found = eagle_submit._locate_semantic(self._issue(segment_index=1), self.task)

        self.assertEqual(found['segment_index'], 1)
        self.assertEqual(found['at'], '02:53')
        self.assertIn('second english', found['source_text'])

    def test_no_segment_index_is_left_unlocated(self):
        self._segment(10, 'a line', 'une ligne')

        found = eagle_submit._locate_semantic(self._issue(segment_index=None), self.task)

        self.assertEqual(found, {})

    def test_an_out_of_range_index_is_left_unlocated(self):
        self._segment(10, 'a line', 'une ligne')

        found = eagle_submit._locate_semantic(self._issue(segment_index=5), self.task)

        self.assertEqual(found, {})


class SemanticAuditFindingsTestCase(TestCase):
    """
    Surfacing a semantic audit that already ran and was already paid for --
    the gap a 20-document review of real translation submissions found:
    dropped paragraphs, invented numbers, and untranslated passages reached
    this preview with zero findings, because only the deterministic checks
    ever ran here. Those defects are exactly what STAGE_AUDIT (the model
    audit in quality/engine.py) already checks for, and for a warm-pool
    translation it already ran, under a quality profile, before a worker
    ever opened this preview -- its verdict just sat unread on the
    revision. `_semantic_audit_findings` reads it; it starts no new audit
    and makes no request of its own (see that function's own docstring).
    """

    def setUp(self):
        super().setUp()
        project = Project.objects.create(name='semantic-audit')
        self.job = TranscriptionJob.objects.create(
            project=project, input_source='upload', status='completed')
        self.task = make_task(kind=Task.TRANSLATION, external_source='translation',
                              external_task_id='5001', status=states.NEW,
                              target_language='fr')
        self.revision = AIRevision.objects.create(
            job=self.job, operation='translate', target_language='fr',
            status='completed')
        # Not what this test class is about -- see build_task's own
        # self_checked docstring for why a "good task" fixture sets this.
        _, checker = make_worker('semanticchecker', 'CHK-SEM')
        Task.objects.filter(pk=self.task.pk).update(
            job=self.job, deliverable_revision=self.revision,
            polished_by=checker, polished_at=timezone.now())
        self.task.refresh_from_db()

    def _segment(self, start, english, french):
        source = TranscriptSegment.objects.create(
            job=self.job, start_time=start, end_time=start + 4, text=english)
        return AIRevisionSegment.objects.create(
            revision=self.revision, original_segment=source,
            start_time=start, end_time=start + 4,
            original_text=english, processed_text=french,
            changed=True, accepted=True)

    def _report(self, status, issues=None, reason=''):
        self.revision.quality_status = status
        self.revision.quality_reason = reason
        self.revision.save(update_fields=['quality_status', 'quality_reason'])
        AIQualityReport.objects.create(
            revision=self.revision, profile='volume', status=status,
            reason=reason, report={'issues': issues or []},
        )

    def test_a_revision_nobody_audited_produces_no_findings(self):
        """The default fixture state: no quality_profile was ever
        requested, so quality_status is 'not_run'. This is the ordinary
        case for most of the platform's history, and it must keep behaving
        exactly as it did before this feature existed."""
        self.assertEqual(eagle_submit._semantic_audit_findings(self.task), [])

    def test_an_audit_still_in_progress_produces_no_findings(self):
        for status in ('pending', 'auditing'):
            self.revision.quality_status = status
            self.revision.save(update_fields=['quality_status'])
            self.assertEqual(
                eagle_submit._semantic_audit_findings(self.task), [],
                f'status={status}')

    def test_a_validated_revision_with_no_report_row_produces_no_findings(self):
        self.revision.quality_status = 'validated'
        self.revision.save(update_fields=['quality_status'])
        self.assertEqual(eagle_submit._semantic_audit_findings(self.task), [])

    def test_a_critical_semantic_issue_is_surfaced_with_its_location(self):
        self._segment(10, 'The witness described three separate incidents.',
                      'Le témoin a décrit un incident.')
        self._report('quality_review_required', issues=[{
            'category': 'completeness', 'severity': 'critical',
            'problem': 'Two of the three incidents in the source are '
                       'missing from the translation.',
            'rule': 'completeness.dropped-content', 'segment_index': 0,
        }])

        findings = eagle_submit._semantic_audit_findings(self.task)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['severity'], 'critical')
        self.assertEqual(findings[0]['code'], 'completeness.dropped-content')
        self.assertEqual(findings[0]['segment_index'], 0)
        self.assertEqual(findings[0]['at'], '00:10')

    def test_a_critical_semantic_finding_blocks_submission(self):
        """The integration point: `run_quality_gate` folds this function's
        output into the same findings list deterministic checks use, so a
        CRITICAL semantic finding blocks exactly the way a CRITICAL
        deterministic one already does -- no new blocking logic needed."""
        self._segment(10, 'The witness described three separate incidents.',
                      'Le témoin a décrit un incident.')
        self._report('quality_review_required', issues=[{
            'category': 'completeness', 'severity': 'critical',
            'problem': 'content dropped',
            'rule': 'completeness.dropped-content', 'segment_index': 0,
        }])
        content = 'Target Language: French\nNumber Of Words: 4\nLe témoin.'

        findings = run_quality_gate(self.task, content)

        self.assertTrue(any(f['severity'] == 'critical' for f in findings))

    def test_a_high_semantic_issue_is_advisory_not_blocking(self):
        self._segment(10, 'source line', 'target line')
        self._report('quality_review_required', issues=[{
            'category': 'linguistic', 'severity': 'high',
            'problem': 'Register is too informal for a legal transcript.',
            'rule': 'linguistic.register', 'segment_index': 0,
        }])
        content = 'Target Language: French\nNumber Of Words: 2\ntarget line'

        findings = run_quality_gate(self.task, content)

        semantic = [f for f in findings if f['code'] == 'linguistic.register']
        self.assertEqual(len(semantic), 1)
        self.assertEqual(semantic[0]['severity'], 'high')
        self.assertFalse(any(f['severity'] == 'critical' for f in findings))

    def test_a_technical_failure_with_no_issues_adds_one_advisory_finding(self):
        self._report('technical_failure', issues=[],
                     reason='The quality pipeline could not complete. The '
                            'AI result above has not been checked.')

        findings = eagle_submit._semantic_audit_findings(self.task)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['code'], 'semantic-audit-incomplete')
        self.assertEqual(findings[0]['severity'], 'high')
        self.assertIn('could not complete', findings[0]['message'])

    def test_review_required_with_no_stored_issues_still_says_something(self):
        """Defensive: the engine's own invariants say this should not
        happen (a refusal always carries its reasons), but a reporting
        layer must not go silent just because an assumption from another
        module turned out to be wrong."""
        self._report('quality_review_required', issues=[])

        findings = eagle_submit._semantic_audit_findings(self.task)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['code'], 'semantic-review-required')

    def test_a_polished_document_suppresses_stale_semantic_findings(self):
        """The audit judged the AI draft. Once a worker has pasted their
        own polished replacement, THAT is what will actually be submitted
        -- and the audit's findings may describe a paragraph that no
        longer exists, exactly the staleness `_segment_pairs` already
        refuses to guess through for deterministic findings."""
        self._segment(10, 'source line', 'MACHINE: target line')
        self._report('quality_review_required', issues=[{
            'category': 'completeness', 'severity': 'critical',
            'problem': 'content dropped',
            'rule': 'completeness.dropped-content', 'segment_index': 0,
        }])
        Task.objects.filter(pk=self.task.pk).update(
            polished_text='POLISHED: target line')
        self.task.refresh_from_db()

        self.assertEqual(eagle_submit._semantic_audit_findings(self.task), [])


class UntranslatedSourceBlocksTestCase(TestCase):
    """
    English left in a French deliverable stops the submission.

    IT USED TO WARN. The gate blocked on `critical` alone, so this finding
    appeared on a real task under the heading "Nothing in this document
    blocks submission" -- one click away from being sent. A tasker following
    the documented flow would have sent it.

    It is not a judgement call the way a register or terminology finding is:
    the source language is either present in the output or it is not, and if
    it is, the platform rejects it. So it is in ALWAYS_BLOCKING rather than
    left to severity.
    """

    def setUp(self):
        super().setUp()
        project = Project.objects.create(name='leakage')
        self.job = TranscriptionJob.objects.create(
            project=project, input_source='upload', status='completed')
        self.task = make_task(kind=Task.TRANSLATION,
                              external_source='translation',
                              external_task_id='4900', status=states.NEW,
                              target_language='fr')
        self.revision = AIRevision.objects.create(
            job=self.job, operation='translate', target_language='fr',
            status='completed')
        Task.objects.filter(pk=self.task.pk).update(
            job=self.job, deliverable_revision=self.revision)
        self.task.refresh_from_db()

    def _segment(self, start, english, french):
        source = TranscriptSegment.objects.create(
            job=self.job, start_time=start, end_time=start + 4, text=english)
        AIRevisionSegment.objects.create(
            revision=self.revision, original_segment=source,
            start_time=start, end_time=start + 4,
            original_text=english, processed_text=french,
            changed=True, accepted=True)

    def test_a_run_of_english_left_in_the_french_stops_the_submission(self):
        """Through the real gate, not by re-implementing its arithmetic."""
        # A long enough English run that the leakage check fires on it.
        english = ('The fundamental theorem of algebra states that every '
                   'polynomial of degree n has exactly n complex roots, and '
                   'this is proved in any advanced course on the subject.')
        self._segment(0, english, english)          # never translated
        self._segment(10, 'And that is the result.',
                      'Et voila le resultat.')

        findings = eagle_submit.run_quality_gate(
            self.task, eagle_submit.render_for_eagle(self.task))
        codes = {f.get('code') for f in findings}

        self.assertIn('leakage.untranslated-source', codes)
        preview = eagle_submit.preview_submission(self.task)
        self.assertTrue(preview.blocking,
                        f'untranslated English did not block: {codes}')

    def test_the_rule_is_in_the_always_blocking_set(self):
        self.assertIn('leakage.untranslated-source', eagle_submit.ALWAYS_BLOCKING)

    def test_other_high_findings_still_only_advise(self):
        """
        An ACCEPTED task carried three findings at these levels, so promoting
        every HIGH would refuse work the platform takes.
        """
        for code in ('typography.fr.nbsp-before-high-punctuation',
                     'entity.identifier-altered',
                     'language.autocorrect-damage'):
            with self.subTest(code=code):
                self.assertNotIn(code, eagle_submit.ALWAYS_BLOCKING)
