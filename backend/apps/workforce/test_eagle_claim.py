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
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from . import states
from .eagle import EagleAuthError, EagleIntegrationService, EagleNotConfigured
from .eagle_claim import (
    RESERVATION_STRANDED_SECONDS, ClaimRefused, InsufficientMinutes,
    _reserve, check_minutes, claim_for_worker, minutes_required_for,
    sweep_stranded_reservations,
)
from .models import Task, TaskEvent
from .tests import make_task, make_worker


class FakeEagle(EagleIntegrationService):
    def __init__(self, minutes=100, accept=True, error=None, deadline_at=None):
        self.minutes = minutes
        self.accept = accept
        self.error = error
        self.claims = []
        # What EagleIntegrationService.claim_task would have read back via
        # list_mine after the real claim -- a plain string, exactly as Eagle
        # sends it, not a datetime. None means "could not be read back",
        # the same as the real implementation's best-effort failure case.
        self.deadline_at = deadline_at

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
        result = {'accepted': True, 'minutes_balance': self.minutes}
        if self.deadline_at is not None:
            result['deadline_at'] = self.deadline_at
        return result


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

    def test_the_deadline_eagle_hands_back_is_persisted_on_claim(self):
        # CONFIRMED LIVE: a worker never saw a timer on any task, from claim
        # to submit, on any screen. DeadlineClock was built, wired in, and
        # tested correctly -- the gap was here. Eagle's own claim response
        # cannot be trusted to carry the deadline it just started (one
        # product's claim call is a bare redirect with no body), so
        # EagleIntegrationService.claim_task reads it back separately and
        # this is where that value has to actually land, or the whole chain
        # was pointless.
        task = eagle_task()

        claim_for_worker(
            task.id, self.worker, self.user,
            service=FakeEagle(minutes=100, deadline_at='2026-09-16T15:00:00Z'),
        )

        task.refresh_from_db()
        self.assertIsNotNone(task.deadline_at)
        self.assertEqual(task.deadline_at.isoformat(), '2026-09-16T15:00:00+00:00')

    def test_a_deadline_eagle_could_not_be_read_back_leaves_the_field_alone(self):
        # The claim must never fail because the read-back did. A missing
        # deadline is a missing timer, not a missing claim.
        task = eagle_task()

        claimed = claim_for_worker(
            task.id, self.worker, self.user, service=FakeEagle(minutes=100),
        )

        self.assertEqual(claimed.status, states.CLAIMED)
        self.assertIsNone(claimed.deadline_at)


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


class ReservationRaceTestCase(TestCase):
    """
    BEFORE_DEPLOYMENT.md item 1: two workers racing the same task used to
    both pass the unlocked "is this free?" read and both send a real claim
    POST to Eagle. The atomic conditional UPDATE in _reserve() closes that
    window before Eagle is ever called -- these tests prove the loser never
    reaches the external call at all, not just that it eventually loses.
    """

    def setUp(self):
        self.user_a, self.worker_a = make_worker('ana', 'W-01')
        self.user_b, self.worker_b = make_worker('bo', 'W-02')

    def test_a_second_concurrent_claim_is_refused_before_eagle_is_called(self):
        task = eagle_task()
        fake = FakeEagle(minutes=100)

        # Simulates worker B's reservation having already landed the instant
        # before worker A's claim_for_worker runs -- exactly what a real
        # concurrent request would produce, without needing actual threads
        # to prove the WHERE clause is what does the excluding.
        self.assertTrue(_reserve(task.id, self.worker_b))

        with self.assertRaises(ClaimRefused):
            claim_for_worker(task.id, self.worker_a, self.user_a, service=fake)

        # The one thing that matters most: the loser never touched Eagle.
        self.assertEqual(fake.claims, [])
        # And the winner's reservation is untouched -- losing does not roll
        # back someone else's in-flight claim.
        task.refresh_from_db()
        self.assertEqual(task.claimed_by_id, self.worker_b.id)
        self.assertIsNotNone(task.claim_reserved_at)

    def test_only_one_reservation_can_ever_win_regardless_of_call_order(self):
        task = eagle_task()

        first = _reserve(task.id, self.worker_a)
        second = _reserve(task.id, self.worker_b)

        self.assertTrue(first)
        self.assertFalse(second)
        task.refresh_from_db()
        self.assertEqual(task.claimed_by_id, self.worker_a.id)


class ReservationRollbackTestCase(TestCase):

    def setUp(self):
        self.user, self.worker = make_worker('ana', 'W-01')

    def test_a_refused_claim_releases_the_reservation_not_just_the_status(self):
        task = eagle_task()

        with self.assertRaises(ClaimRefused):
            claim_for_worker(task.id, self.worker, self.user,
                             service=FakeEagle(accept=False))

        task.refresh_from_db()
        self.assertIsNone(task.claimed_by_id)
        self.assertIsNone(task.claim_reserved_at)

    def test_an_unexpected_exception_still_rolls_back_the_reservation(self):
        """
        The finally-style guard is generic on purpose: a timeout, a dropped
        connection, or a bug in the client all fall outside the two
        Eagle-specific except clauses claim_for_worker already handles, and
        none of them may be allowed to strand a reservation for the full
        60s until the sweeper runs.
        """
        task = eagle_task()
        fake = FakeEagle(error=RuntimeError('the network fell over'))

        with self.assertRaises(RuntimeError):
            claim_for_worker(task.id, self.worker, self.user, service=fake)

        task.refresh_from_db()
        self.assertIsNone(task.claimed_by_id)
        self.assertIsNone(task.claim_reserved_at)

    def test_an_insufficient_minutes_refusal_also_releases_the_reservation(self):
        task = eagle_task(duration_seconds=1540)

        with self.assertRaises(InsufficientMinutes):
            claim_for_worker(task.id, self.worker, self.user, service=FakeEagle(minutes=5))

        task.refresh_from_db()
        self.assertIsNone(task.claimed_by_id)
        self.assertIsNone(task.claim_reserved_at)


class StrandedReservationSweepTestCase(TestCase):

    def setUp(self):
        self.user, self.worker = make_worker('ana', 'W-01')

    def _reserved(self, seconds_ago, **kwargs):
        task = eagle_task(**kwargs)
        Task.objects.filter(pk=task.id).update(
            claimed_by=self.worker,
            claim_reserved_at=timezone.now() - timedelta(seconds=seconds_ago),
        )
        task.refresh_from_db()
        return task

    def test_a_reservation_older_than_the_threshold_is_swept(self):
        stranded = self._reserved(RESERVATION_STRANDED_SECONDS + 30, external_task_id='1')

        released = sweep_stranded_reservations()

        self.assertEqual(released, 1)
        stranded.refresh_from_db()
        self.assertIsNone(stranded.claimed_by_id)
        self.assertIsNone(stranded.claim_reserved_at)

    def test_a_reservation_inside_the_threshold_is_left_alone(self):
        """
        A worker mid-round-trip -- checking minutes, then calling Eagle --
        must not have its reservation snatched out from under it just
        because a sweep happens to run at the same moment.
        """
        fresh = self._reserved(10, external_task_id='2')

        released = sweep_stranded_reservations()

        self.assertEqual(released, 0)
        fresh.refresh_from_db()
        self.assertEqual(fresh.claimed_by_id, self.worker.id)
        self.assertIsNotNone(fresh.claim_reserved_at)

    def test_a_completed_claim_is_never_touched_however_old(self):
        """
        services.claim_task clears claim_reserved_at as part of the same
        update that moves status to CLAIMED, so a real, finished claim from
        hours ago must not be mistaken for a stranded one.
        """
        task = eagle_task(external_task_id='3')
        from . import services
        claimed = services.claim_task(task.id, worker=self.worker, actor=self.user)
        self.assertIsNone(claimed.claim_reserved_at)  # sanity on the fixture itself

        released = sweep_stranded_reservations()

        self.assertEqual(released, 0)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, states.CLAIMED)
        self.assertEqual(claimed.claimed_by_id, self.worker.id)

    def test_a_stranded_reservation_on_a_non_claimable_status_is_left_alone(self):
        """
        status__in=CLAIMABLE_STATES is deliberate: claim_reserved_at is only
        ever meaningful mid-reservation, but guarding on status too means a
        task that moved on for some other reason is never touched here
        regardless of what an old, otherwise-unrelated timestamp says.
        """
        task = self._reserved(RESERVATION_STRANDED_SECONDS + 30,
                              external_task_id='4', status=states.SUBMITTED)

        released = sweep_stranded_reservations()

        self.assertEqual(released, 0)
        task.refresh_from_db()
        self.assertEqual(task.claimed_by_id, self.worker.id)


class LocalTaskTestCase(TestCase):
    """A task with no external id is ours already; nothing to claim outside."""

    def test_a_local_task_claims_without_calling_eagle(self):
        user, worker = make_worker('ana', 'W-01')
        task = make_task(status=states.NEW, external_task_id='', external_source='')
        fake = FakeEagle()

        claimed = claim_for_worker(task.id, worker, user, service=fake)

        self.assertEqual(claimed.status, states.CLAIMED)
        self.assertEqual(fake.claims, [])
