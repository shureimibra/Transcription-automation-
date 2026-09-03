"""
Claiming on Eagle -- the path that spends real money.

WHAT THESE PROTECT, in order of what a mistake costs:

  * ORDER. Eagle is called first and our state moves only on
    acknowledgement. Marking a task claimed locally when Eagle refused would
    put a worker to work on a task they do not hold, and their hour of
    editing would submit into nothing.
  * AFFORDABILITY. A claim that cannot be paid for is refused before the
    external call, and the refusal blames the account, not the worker.
  * RESTRAINT. Nothing claims speculatively. Every claim is one worker
    asking for one task.

The Eagle side is a fake throughout. Claiming for real starts a one-hour
deadline and deducts purchased minutes; a test suite must never do that.
"""
from unittest import mock

from django.test import TestCase

from . import states
from .eagle import EagleAuthError, EagleIntegrationService, EagleNotConfigured
from .eagle_claim import (
    ClaimRefused, InsufficientMinutes, check_minutes, claim_for_worker,
    minutes_required_for,
)
from .models import Task, TaskEvent
from .tests import make_task, make_worker


class FakeEagle(EagleIntegrationService):
    def __init__(self, minutes=100, accept=True, error=None):
        self.minutes = minutes
        self.accept = accept
        self.error = error
        self.claims = []

    def is_configured(self):
        return True

    def minutes_balance(self, source):
        return self.minutes

    def claim_task(self, external_id, source=''):
        if self.error:
            raise self.error
        self.claims.append((source, external_id))
        if not self.accept:
            return {'accepted': False}
        self.minutes -= 10
        return {'accepted': True, 'minutes_balance': self.minutes}


def eagle_task(**kwargs):
    fields = {
        'external_source': 'translation',
        'external_task_id': '4333',
        'duration_seconds': 618,
        'status': states.NEW,
    }
    fields.update(kwargs)
    return make_task(**fields)


class MinutesArithmeticTestCase(TestCase):

    def test_minutes_round_up_because_platforms_bill_whole_minutes(self):
        # 10.3 minutes billed as 10 would produce a claim that fails after a
        # worker has already been told to start.
        self.assertEqual(minutes_required_for(make_task(duration_seconds=618)), 11)
        self.assertEqual(minutes_required_for(make_task(duration_seconds=600)), 10)
        self.assertEqual(minutes_required_for(make_task(duration_seconds=61)), 2)

    def test_an_unknown_duration_costs_nothing_known(self):
        self.assertEqual(minutes_required_for(make_task(duration_seconds=None)), 0)

    def test_a_short_task_is_affordable_on_a_small_balance(self):
        """
        The reason the floor is dynamic. A flat "keep 25 minutes" rule would
        idle the team while three-minute work sat claimable.
        """
        task = eagle_task(duration_seconds=120)

        check = check_minutes(task, service=FakeEagle(minutes=5))

        self.assertTrue(check.sufficient)
        self.assertEqual(check.required, 2)

    def test_a_long_task_is_refused_on_the_same_balance(self):
        task = eagle_task(duration_seconds=1540)

        check = check_minutes(task, service=FakeEagle(minutes=5))

        self.assertFalse(check.sufficient)
        self.assertIn('26', check.reason)

    def test_an_unknown_balance_does_not_block_work(self):
        # Refusing everything because a number is absent is worse than
        # letting the platform decline for itself.
        task = eagle_task()

        check = check_minutes(task, service=FakeEagle(minutes=None))

        self.assertTrue(check.sufficient)
        self.assertIn('unknown', check.reason)


class ClaimOrderingTestCase(TestCase):

    def setUp(self):
        self.user, self.worker = make_worker('ana', 'W-01')

    def test_a_successful_claim_calls_eagle_then_moves_our_state(self):
        task = eagle_task()
        fake = FakeEagle(minutes=100)

        claimed = claim_for_worker(task.id, self.worker, self.user, service=fake)

        self.assertEqual(fake.claims, [('translation', '4333')])
        self.assertEqual(claimed.status, states.CLAIMED)
        self.assertEqual(claimed.claimed_by_id, self.worker.id)

    def test_our_state_does_not_move_when_eagle_refuses(self):
        """
        The failure that would hurt most: a worker editing a task they do
        not hold, submitting into nothing an hour later.
        """
        task = eagle_task()

        with self.assertRaises(ClaimRefused):
            claim_for_worker(task.id, self.worker, self.user,
                             service=FakeEagle(accept=False))

        task.refresh_from_db()
        self.assertEqual(task.status, states.NEW)
        self.assertIsNone(task.claimed_by_id)

    def test_our_state_does_not_move_when_eagle_is_unreachable(self):
        task = eagle_task()
        fake = FakeEagle(error=EagleAuthError('session rejected'))

        with self.assertRaises(ClaimRefused):
            claim_for_worker(task.id, self.worker, self.user, service=fake)

        task.refresh_from_db()
        self.assertEqual(task.status, states.NEW)

    def test_an_unconfigured_platform_refuses_without_touching_state(self):
        task = eagle_task()
        fake = FakeEagle(error=EagleNotConfigured('nothing stored'))

        with self.assertRaises(ClaimRefused):
            claim_for_worker(task.id, self.worker, self.user, service=fake)

        task.refresh_from_db()
        self.assertEqual(task.status, states.NEW)

    def test_the_claim_is_audited_with_what_it_cost(self):
        task = eagle_task()

        claim_for_worker(task.id, self.worker, self.user, service=FakeEagle(minutes=100))

        events = TaskEvent.objects.filter(task=task, event_type=TaskEvent.STATUS_CHANGED)
        self.assertTrue(events.exists())
        metadata = events.first().metadata
        self.assertEqual(metadata['minutes_spent'], 11)
        self.assertEqual(metadata['external_task_id'], '4333')


class ClaimRefusalTestCase(TestCase):

    def setUp(self):
        self.user, self.worker = make_worker('ana', 'W-01')

    def test_an_unaffordable_task_is_refused_before_eagle_is_called(self):
        task = eagle_task(duration_seconds=1540)
        fake = FakeEagle(minutes=5)

        with self.assertRaises(InsufficientMinutes):
            claim_for_worker(task.id, self.worker, self.user, service=fake)

        # The point: no external call happened, so no round-trip and no
        # chance of a half-completed claim.
        self.assertEqual(fake.claims, [])

    def test_the_minutes_refusal_blames_the_account_not_the_worker(self):
        task = eagle_task(duration_seconds=1540)

        try:
            claim_for_worker(task.id, self.worker, self.user, service=FakeEagle(minutes=5))
            self.fail('should have refused')
        except InsufficientMinutes as exc:
            message = exc.safe_message

        self.assertIn('administrator', message.lower())
        # Nothing implying the worker did something wrong.
        self.assertNotIn('you already', message.lower())

    def test_a_minutes_refusal_is_recorded_for_the_operator(self):
        task = eagle_task(duration_seconds=1540)

        with self.assertRaises(InsufficientMinutes):
            claim_for_worker(task.id, self.worker, self.user, service=FakeEagle(minutes=5))

        event = TaskEvent.objects.filter(task=task, success=False).first()
        self.assertIsNotNone(event, 'the refusal left no trace for the operator')
        self.assertEqual(event.metadata['minutes_required'], 26)

    def test_a_task_someone_else_holds_is_refused(self):
        other_user, other = make_worker('bo', 'W-02')
        task = eagle_task()
        from . import services
        services.claim_task(task.id, worker=other, actor=other_user)
        fake = FakeEagle()

        with self.assertRaises(ClaimRefused):
            claim_for_worker(task.id, self.worker, self.user, service=fake)

        self.assertEqual(fake.claims, [])

    def test_a_worker_at_their_limit_is_refused_before_spending(self):
        self.worker.max_concurrent_tasks = 1
        self.worker.save(update_fields=['max_concurrent_tasks'])
        from . import services
        services.claim_task(eagle_task(external_task_id='1').id,
                            worker=self.worker, actor=self.user)
        fake = FakeEagle()

        with self.assertRaises(ClaimRefused) as caught:
            claim_for_worker(eagle_task(external_task_id='2').id,
                             self.worker, self.user, service=fake)

        self.assertIn('finish or release', caught.exception.safe_message.lower())
        self.assertEqual(fake.claims, [])


class LocalTaskTestCase(TestCase):
    """A task with no external id is ours already; nothing to claim outside."""

    def test_a_local_task_claims_without_calling_eagle(self):
        user, worker = make_worker('ana', 'W-01')
        task = make_task(status=states.NEW, external_task_id='', external_source='')
        fake = FakeEagle()

        claimed = claim_for_worker(task.id, worker, user, service=fake)

        self.assertEqual(claimed.status, states.CLAIMED)
        self.assertEqual(fake.claims, [])
