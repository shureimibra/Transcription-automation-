"""
The Eagle sync poller, against a fake Eagle.

WHAT THESE PROTECT:

  * The poll is a READ. It must never claim, never submit, never spend a
    minute, and never move a task a worker is holding.
  * It must be idempotent. Two cycles over the same offer produce one task,
    because the alternative is the same work handed to two people.
  * A dead session must degrade, not detonate. A poller that raises on a
    stale cookie takes down the queue for workers whose claimed tasks are
    still perfectly workable.
"""
import datetime as dt
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from . import states
from .credentials import TRANSCRIPTION, TRANSLATION
from .eagle import (EagleAuthError, EagleIntegrationError,
                    EagleIntegrationService, EagleNotConfigured, EagleTask)
from .models import Task, TaskEvent, WorkerProfile
from .sync import sync_all, sync_product
from .tests import make_task, make_worker


def offer(external_id, product=TRANSLATION, kind='translation', duration=618,
          title='C 226.mp3', minutes_required=10, payout=3.0):
    return EagleTask(
        external_id=str(external_id), source=product, title=title, kind=kind,
        duration_seconds=duration,
        page_url=f'https://example.test/tasks/{external_id}',
        raw={'minutes_required': minutes_required, 'payout': payout,
             'media_url': f'https://example.test/{external_id}.mp3',
             'media_needs_session': product == TRANSLATION},
    )


class FakeEagle(EagleIntegrationService):
    """
    A stand-in that records everything asked of it.

    `claims` and `submissions` exist so a test can assert they stayed
    empty -- the strongest available statement that a read-only component
    stayed read-only.
    """

    name = 'fake'

    def __init__(self, available=None, minutes=100, healthy=True):
        self.available = available or []
        self.minutes = minutes
        self.healthy = healthy
        self.claims = []
        self.submissions = []
        self.health_checks = 0

    def is_configured(self):
        return True

    def check_session(self, source):
        self.health_checks += 1
        if not self.healthy:
            raise EagleAuthError('session rejected')
        return {'product': source, 'status': 'active',
                'account': 'Shurz', 'minutes_balance': self.minutes}

    def list_available_tasks(self, source=''):
        return [t for t in self.available if t.source == source]

    def claim_task(self, external_id, source=''):
        self.claims.append((source, external_id))
        return {'accepted': True}

    def submit_task(self, external_id, content, source='', metadata=None):
        self.submissions.append((source, external_id))
        raise AssertionError('the poller must never submit')


class SyncMirroringTestCase(TestCase):

    def test_an_offer_becomes_a_task(self):
        fake = FakeEagle(available=[offer(4333)])

        report = sync_product(TRANSLATION, service=fake)

        self.assertEqual(report.created, 1)
        task = Task.objects.get(external_task_id='4333')
        self.assertEqual(task.external_source, TRANSLATION)
        self.assertEqual(task.status, states.NEW)
        self.assertEqual(task.kind, Task.TRANSLATION)
        self.assertEqual(task.duration_seconds, 618)

    def test_running_twice_creates_one_task(self):
        """Idempotency is the property the unique index exists for."""
        fake = FakeEagle(available=[offer(4333)])

        sync_product(TRANSLATION, service=fake)
        second = sync_product(TRANSLATION, service=fake)

        self.assertEqual(Task.objects.filter(external_task_id='4333').count(), 1)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 1)

    def test_the_same_id_on_the_two_products_is_two_tasks(self):
        # The products number their tasks independently, so 4333 exists on
        # both and means different things.
        fake = FakeEagle(available=[
            offer(4333, product=TRANSLATION, kind='translation'),
            offer(4333, product=TRANSCRIPTION, kind='transcription'),
        ])

        sync_all(service=fake)

        self.assertEqual(Task.objects.filter(external_task_id='4333').count(), 2)

    def test_creation_is_audited(self):
        fake = FakeEagle(available=[offer(4333)])

        sync_product(TRANSLATION, service=fake)

        task = Task.objects.get(external_task_id='4333')
        events = TaskEvent.objects.filter(task=task)
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().event_type, TaskEvent.TASK_CREATED)

    def test_descriptive_fields_are_refreshed_on_a_later_cycle(self):
        fake = FakeEagle(available=[offer(4333, title='old.mp3')])
        sync_product(TRANSLATION, service=fake)

        fake.available = [offer(4333, title='new.mp3')]
        sync_product(TRANSLATION, service=fake)

        self.assertEqual(Task.objects.get(external_task_id='4333').title, 'new.mp3')


class SyncIsReadOnlyTestCase(TestCase):
    """The poll must not touch Eagle beyond reading, nor our own workflow."""

    def test_it_never_claims_and_never_submits(self):
        fake = FakeEagle(available=[offer(1), offer(2), offer(3)])

        sync_product(TRANSLATION, service=fake)

        self.assertEqual(fake.claims, [], 'the poller claimed a task')
        self.assertEqual(fake.submissions, [], 'the poller submitted work')

    def test_it_does_not_disturb_a_task_a_worker_is_holding(self):
        user, profile = make_worker('ana', 'W-01')
        fake = FakeEagle(available=[offer(4333)])
        sync_product(TRANSLATION, service=fake)

        task = Task.objects.get(external_task_id='4333')
        from . import services
        services.claim_task(task.id, worker=profile, actor=user)

        # It has vanished from Eagle's available list -- because WE took it.
        fake.available = []
        sync_product(TRANSLATION, service=fake)

        task.refresh_from_db()
        self.assertEqual(task.status, states.CLAIMED)
        self.assertEqual(task.claimed_by_id, profile.id)

    def test_a_task_taken_by_another_platform_worker_is_retired(self):
        fake = FakeEagle(available=[offer(4333)])
        sync_product(TRANSLATION, service=fake)

        fake.available = []
        report = sync_product(TRANSLATION, service=fake)

        task = Task.objects.get(external_task_id='4333')
        self.assertEqual(report.taken_by_others, 1)
        self.assertEqual(task.status, states.CANCELLED)
        # Retired, not deleted: losing races is a rate worth measuring.
        self.assertTrue(Task.objects.filter(external_task_id='4333').exists())

    def test_retiring_goes_through_the_state_machine_and_is_audited(self):
        fake = FakeEagle(available=[offer(4333)])
        sync_product(TRANSLATION, service=fake)
        fake.available = []
        sync_product(TRANSLATION, service=fake)

        task = Task.objects.get(external_task_id='4333')
        event = TaskEvent.objects.filter(
            task=task, event_type=TaskEvent.TASK_CANCELLED).first()
        self.assertIsNotNone(event, 'retirement was not audited')
        self.assertEqual(event.from_status, states.NEW)
        self.assertEqual(event.to_status, states.CANCELLED)


class SyncDegradesGracefullyTestCase(TestCase):

    def test_an_expired_session_is_reported_not_raised(self):
        fake = FakeEagle(available=[offer(4333)], healthy=False)

        report = sync_product(TRANSLATION, service=fake)

        self.assertFalse(report.session_ok)
        self.assertTrue(report.errors)
        self.assertEqual(report.created, 0)

    def test_an_unconfigured_credential_is_reported_not_raised(self):
        class Unconfigured(EagleIntegrationService):
            def check_session(self, source):
                raise EagleNotConfigured('nothing stored')

        report = sync_product(TRANSLATION, service=Unconfigured())

        self.assertFalse(report.session_ok)
        self.assertEqual(report.created, 0)

    def test_a_dead_session_leaves_existing_tasks_alone(self):
        """Workers holding tasks must keep working through an outage."""
        fake = FakeEagle(available=[offer(4333)])
        sync_product(TRANSLATION, service=fake)

        fake.healthy = False
        sync_product(TRANSLATION, service=fake)

        self.assertEqual(Task.objects.get(external_task_id='4333').status, states.NEW)

    def test_one_malformed_offer_does_not_stop_the_cycle(self):
        good, bad = offer(1), offer(2)
        bad.raw = None
        bad.duration_seconds = 'not-a-number'
        fake = FakeEagle(available=[bad, good])

        report = sync_product(TRANSLATION, service=fake)

        self.assertEqual(report.skipped, 1)
        self.assertTrue(Task.objects.filter(external_task_id='1').exists())

    def test_the_health_check_runs_every_cycle_because_it_is_the_keep_alive(self):
        fake = FakeEagle(available=[])

        sync_product(TRANSLATION, service=fake)
        sync_product(TRANSLATION, service=fake)

        self.assertEqual(fake.health_checks, 2)

    def test_the_report_carries_the_minutes_balance_for_the_operator(self):
        fake = FakeEagle(available=[offer(1)], minutes=15)

        report = sync_product(TRANSLATION, service=fake)

        self.assertEqual(report.minutes_balance, 15)

class OutcomeReconciliationTestCase(TestCase):
    """
    The platform's verdict has to come back to us.

    Until this existed the sync only read the AVAILABLE list, so a task fell
    out of view the moment it was submitted and its status froze at
    `submitted` for ever. Eagle knew whether the work was accepted -- its
    awarded feed says `completed` or `rejected`, with a reason -- and none of
    it reached the worker who did the job or the person deciding what to pay
    them.
    """

    def setUp(self):
        self.user, self.worker = make_worker('ana', 'W-01')

    def _service(self, rows):
        class Awarded(FakeEagle):
            def list_available_tasks(inner, source=''):
                return []
            def list_my_tasks(inner, source=''):
                return [
                    EagleTask(external_id=str(i), source=source,
                              raw={'external_status': st, 'reject_reason': why})
                    for i, st, why in rows
                ]
        return Awarded()

    def _submitted(self, external_id):
        return make_task(external_source='translation', external_task_id=external_id,
                         status=states.SUBMITTED, claimed_by=self.worker)

    def test_an_accepted_submission_comes_back_as_approved(self):
        task = self._submitted('4452')

        report = sync_product('translation', service=self._service([('4452', 'completed', '')]))

        task.refresh_from_db()
        self.assertEqual(task.status, states.APPROVED)
        self.assertEqual(report.reviewed, 1)

    def test_a_failed_review_comes_back_with_the_platform_reason(self):
        task = self._submitted('4130')

        sync_product('translation', service=self._service(
            [('4130', 'rejected', 'Speaker labels were wrong throughout.')]))

        task.refresh_from_db()
        self.assertEqual(task.status, states.REJECTED)
        self.assertIn('Speaker labels', task.rejection_reason)

    def test_a_submission_still_under_review_is_left_alone(self):
        # `submitted` on the feed means the review has NOT happened. Treating
        # it as an outcome would report every pending task as decided.
        task = self._submitted('4639')

        report = sync_product('translation', service=self._service([('4639', 'submitted', '')]))

        task.refresh_from_db()
        self.assertEqual(task.status, states.SUBMITTED)
        self.assertEqual(report.reviewed, 0)

    def test_work_a_tasker_is_still_holding_is_never_touched(self):
        # An outcome is news about FINISHED work. A poll reaching into
        # something in progress would take a task out of someone's hands
        # mid-sentence.
        task = make_task(external_source='translation', external_task_id='4700',
                         status=states.IN_PROGRESS, claimed_by=self.worker)

        sync_product('translation', service=self._service([('4700', 'completed', '')]))

        task.refresh_from_db()
        self.assertEqual(task.status, states.IN_PROGRESS)


class UndeliveredSubmissionTestCase(TestCase):
    """
    Work we think we sent that the platform never received.

    THE HOLE THIS CLOSES. Outcome reconciliation acts only on rows the
    awarded feed returns, so it answers "the platform decided" and has no
    answer for "the platform never heard of this". A submission that did not
    land sat at `submitted` for ever -- shown as "Awaiting review" to the
    tasker, and counted as delivered work when somebody decided awards.

    Measured against the live account: Eagle's transcription feed listed one
    task; ours listed five. Three had been claimed cold that morning and
    marked submitted through the record-only button after their deadlines
    lapsed; one had been waiting six days.

    The danger runs the other way too -- telling somebody their finished
    work was lost when it was merely still in flight -- so every test below
    that asserts we DO act is matched by one asserting we do not.
    """

    def setUp(self):
        self.user, self.worker = make_worker('ana', 'W-01')

    def _service(self, rows, explode=False):
        class Awarded(FakeEagle):
            def list_available_tasks(inner, source=''):
                return []
            def list_my_tasks(inner, source=''):
                if explode:
                    raise EagleIntegrationError('the feed is down')
                return [EagleTask(external_id=str(i), source=source,
                                  raw={'external_status': 'submitted'})
                        for i in rows]
        return Awarded()

    def _submitted(self, external_id, hours_past_deadline):
        task = make_task(external_source='translation',
                         external_task_id=external_id,
                         status=states.SUBMITTED, claimed_by=self.worker)
        when = timezone.now() - dt.timedelta(hours=hours_past_deadline)
        Task.objects.filter(pk=task.pk).update(deadline_at=when, submitted_at=when)
        task.refresh_from_db()
        return task

    def test_a_submission_the_platform_never_received_is_marked_failed(self):
        task = self._submitted('37593', hours_past_deadline=48)

        report = sync_product('translation', service=self._service([]))

        task.refresh_from_db()
        self.assertEqual(task.status, states.FAILED)
        self.assertEqual(report.undelivered, 1)
        # The worker is told what actually happened, in plain words.
        self.assertIn('not delivered', task.failure_reason.lower())

    def test_a_submission_still_inside_the_grace_period_is_left_alone(self):
        """Absence is only meaningful once the deadline is well past."""
        task = self._submitted('4625', hours_past_deadline=1)

        report = sync_product('translation', service=self._service([]))

        task.refresh_from_db()
        self.assertEqual(task.status, states.SUBMITTED)
        self.assertEqual(report.undelivered, 0)

    def test_a_submission_the_feed_still_lists_is_never_touched(self):
        """However old it is: the platform has it, so it landed."""
        task = self._submitted('37589', hours_past_deadline=200)

        sync_product('translation', service=self._service(['37589']))

        task.refresh_from_db()
        self.assertEqual(task.status, states.SUBMITTED)

    def test_a_feed_that_could_not_be_read_flags_nothing(self):
        """
        THE MOST IMPORTANT ONE. If the listing failed, every task looks
        absent -- and acting on that would mark a whole day's delivered work
        as lost on the strength of one network error.
        """
        task = self._submitted('37593', hours_past_deadline=48)

        report = sync_product('translation', service=self._service([], explode=True))

        task.refresh_from_db()
        self.assertEqual(task.status, states.SUBMITTED)
        self.assertEqual(report.undelivered, 0)

    def test_work_that_was_never_submitted_is_out_of_scope(self):
        """A task still being worked on is not a delivery that went missing."""
        task = make_task(external_source='translation', external_task_id='4700',
                         status=states.CLAIMED, claimed_by=self.worker)
        Task.objects.filter(pk=task.pk).update(
            deadline_at=timezone.now() - dt.timedelta(hours=48))

        sync_product('translation', service=self._service([]))

        task.refresh_from_db()
        self.assertEqual(task.status, states.CLAIMED)
