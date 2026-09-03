"""
Six taskers, one warm task, the same instant.

WHY THIS TEST EXISTS. The queue is shared and it empties fast: measured on a
live day, 75% of the translation pool went to other people's taskers within
four hours. Everyone sorts by "Ready now", so everyone reaches for the same
card -- and with six people on one platform the collision is not an edge
case, it is the normal shape of a busy morning.

What must hold when they collide:

  1. EXACTLY ONE WINNER. Never two holders, never a task claimed twice.
  2. THE LOSERS ARE TOLD SOMETHING TRUE AND ACTIONABLE. Not a stack trace,
     not a generic failure -- "someone already started that" and get on with
     the next one.
  3. IT IS A CONFLICT, NOT AN ERROR. 409, so the interface can say "taken"
     rather than "something went wrong", which is what turns a normal race
     into apparent chaos.
  4. NOBODY IS LEFT HOLDING A BROKEN TASK. A loser's own state is untouched
     and they are free to claim something else immediately.

The threads are released together by a Barrier rather than merely started
together: without it the first finishes before the second begins and the
test passes for the wrong reason.
"""
import threading

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from . import states
from .eagle import EagleIntegrationService, EagleSubmissionResult, EagleTask
from .models import Task, WorkerProfile
from .tests import make_task, make_worker

TASKERS = 6


class OnePerTask(EagleIntegrationService):
    """
    The platform, as it actually behaves: the first claim wins and every
    later one is refused. This is what really protects us today -- our own
    lock is taken AFTER the external call, so Eagle's refusal is the thing
    standing between us and a double claim.
    """

    name = 'one-per-task'

    def __init__(self):
        self._lock = threading.Lock()
        self._claimed = set()
        self.calls = 0

    def is_configured(self):
        return True

    def get_task(self, external_id, source=''):
        return EagleTask(external_id=str(external_id), source=source,
                         raw={'external_status': 'available'})

    def minutes_balance(self, source=''):
        return 999

    def claim_task(self, external_id, source=''):
        with self._lock:
            self.calls += 1
            if external_id in self._claimed:
                return {'accepted': False,
                        'message': 'This task was just taken by another user.'}
            self._claimed.add(external_id)
            return {'accepted': True}

    def submit_task(self, external_id, content, source='', metadata=None):
        return EagleSubmissionResult(accepted=True, reference=str(external_id))


class SixTaskersOneTaskTestCase(TransactionTestCase):
    """TransactionTestCase, because threads need real committed rows."""

    reset_sequences = True

    def setUp(self):
        self.people = []
        for index in range(TASKERS):
            user, worker = make_worker(f'tasker{index}', f'W-{index:02d}')
            self.people.append((user, worker))
        self.task = make_task(external_task_id='9001', status=states.NEW,
                              duration_seconds=300)

    def _race(self, service):
        """Every tasker POSTs the claim at the same instant."""
        barrier = threading.Barrier(TASKERS)
        results = {}

        def attempt(user, worker):
            client = APIClient()
            client.force_authenticate(user=user)
            try:
                barrier.wait()
                response = client.post(f'/api/v1/workforce/tasks/{self.task.id}/claim/')
                results[worker.worker_code] = (
                    response.status_code,
                    (response.data or {}).get('error', '') if hasattr(response, 'data') else '',
                )
            finally:
                connections.close_all()

        threads = [threading.Thread(target=attempt, args=(u, w), name=w.worker_code)
                   for u, w in self.people]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def test_exactly_one_tasker_wins_and_the_rest_are_told_plainly(self):
        service = OnePerTask()
        with self.settings(EAGLE_SERVICE_OVERRIDE=service):
            from unittest import mock
            with mock.patch('apps.workforce.eagle.get_eagle_service', return_value=service):
                results = self._race(service)

        winners = [code for code, (status, _) in results.items() if status == 200]
        losers = [code for code, (status, _) in results.items() if status != 200]

        self.assertEqual(len(winners), 1, f'expected one winner, got {results}')
        self.assertEqual(len(losers), TASKERS - 1)

        # 1. One holder in the database, and it is the winner.
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, states.CLAIMED)
        self.assertEqual(self.task.claimed_by.worker_code, winners[0])

        # 2. Every loser gets 409 -- a conflict, not a failure. This is what
        #    lets the interface say "taken" instead of "something broke".
        for code in losers:
            status, message = results[code]
            self.assertEqual(status, 409, f'{code} got {status}: {message}')

            # 3. And a sentence a person can act on, naming no internals.
            self.assertTrue(message, f'{code} got an empty message')
            self.assertNotIn('Traceback', message)
            self.assertNotIn(str(self.task.id), message)
            self.assertRegex(
                message.lower(),
                r'already|no longer|someone else|taken',
                f'{code} got an unhelpful message: {message!r}',
            )

        # 4. No loser is left holding anything, so all five can immediately
        #    claim something else.
        held = Task.objects.filter(claimed_by__isnull=False).count()
        self.assertEqual(held, 1)

    def test_a_task_taken_outside_our_platform_refuses_every_one_of_them(self):
        # The commoner case: another company's tasker got there first, so the
        # platform refuses everybody. Nobody may end up holding it locally.
        service = OnePerTask()
        service.claim_task('9001')          # somebody else, moments earlier

        from unittest import mock
        with mock.patch('apps.workforce.eagle.get_eagle_service', return_value=service):
            results = self._race(service)

        self.assertTrue(all(status == 409 for status, _ in results.values()),
                        f'expected every claim refused, got {results}')

        self.task.refresh_from_db()
        self.assertIsNone(self.task.claimed_by)
        self.assertEqual(self.task.status, states.NEW)
