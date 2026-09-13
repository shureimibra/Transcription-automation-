"""
Workforce layer tests.

THE CONCURRENCY TESTS ARE THE POINT OF THIS FILE. Everything else here is
ordinary CRUD and permission checking that would be caught by review;
"two workers cannot claim the same task" is the one guarantee that is
invisible on inspection and expensive to get wrong.

Those tests SKIP THEMSELVES ON SQLITE rather than passing. `select_for_update`
is a silent no-op there, so a green run would assert a guarantee that had
not been exercised -- worse than no test, because it would be believed. Run
them with DB_ENGINE=postgres.
"""
import threading

from django.contrib.auth.models import User
from django.db import connection, connections, transaction
from unittest import mock

from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient, APITestCase

from . import services, states
from .models import Task, TaskEvent, WorkerProfile


def make_worker(username, code, is_admin=False, status=WorkerProfile.ACTIVE,
                max_concurrent=3):
    user = User.objects.create_user(
        username=username, password='an-adequately-long-passphrase',
        is_staff=is_admin,
    )
    profile = WorkerProfile.objects.create(
        user=user, worker_code=code, display_name=username.title(),
        status=status, max_concurrent_tasks=max_concurrent,
    )
    return user, profile


def make_task(**kwargs):
    fields = {'kind': Task.TRANSCRIPTION, 'title': 'A task'}
    fields.update(kwargs)
    return Task.objects.create(**fields)


# ===================================================================== #
# State machine
# ===================================================================== #

class StateMachineTestCase(TestCase):
    """
    The machine is a table, so it can be asserted against directly rather
    than by driving the API for every combination.
    """

    def test_the_documented_happy_path_is_legal_end_to_end(self):
        path = [
            (states.NEW, states.ASSIGNED, states.ROLE_ADMIN),
            (states.ASSIGNED, states.CLAIMED, states.ROLE_WORKER),
            (states.CLAIMED, states.IN_PROGRESS, states.ROLE_WORKER),
            (states.IN_PROGRESS, states.REVIEW, states.ROLE_WORKER),
            (states.REVIEW, states.READY_TO_SUBMIT, states.ROLE_WORKER),
            (states.READY_TO_SUBMIT, states.SUBMITTED, states.ROLE_WORKER),
            (states.SUBMITTED, states.APPROVED, states.ROLE_ADMIN),
        ]
        for source, target, role in path:
            with self.subTest(move=f'{source}->{target}'):
                self.assertTrue(states.can_transition(source, target, role))

    def test_undefined_transitions_are_refused(self):
        """Skipping the workflow is not a permissions question -- these
        moves simply do not exist."""
        for source, target in [
            (states.NEW, states.SUBMITTED),
            (states.NEW, states.APPROVED),
            (states.REJECTED, states.APPROVED),
            (states.CLAIMED, states.APPROVED),
            (states.APPROVED, states.IN_PROGRESS),
        ]:
            with self.subTest(move=f'{source}->{target}'):
                self.assertFalse(states.can_transition(source, target, states.ROLE_ADMIN))

    def test_a_worker_cannot_approve_or_reject(self):
        """
        The outcome of work is never decided by whoever did it. Encoded in
        the machine rather than checked at one endpoint, so a future
        endpoint inherits the rule.
        """
        self.assertFalse(states.can_transition(states.SUBMITTED, states.APPROVED, states.ROLE_WORKER))
        self.assertFalse(states.can_transition(states.SUBMITTED, states.REJECTED, states.ROLE_WORKER))
        self.assertTrue(states.can_transition(states.SUBMITTED, states.APPROVED, states.ROLE_ADMIN))

    def test_a_worker_cannot_assign_or_cancel(self):
        self.assertFalse(states.can_transition(states.NEW, states.ASSIGNED, states.ROLE_WORKER))
        self.assertFalse(states.can_transition(states.NEW, states.CANCELLED, states.ROLE_WORKER))

    def test_admin_inherits_every_worker_transition(self):
        for (source, target), roles in states.TRANSITIONS.items():
            if states.ROLE_WORKER in roles:
                with self.subTest(move=f'{source}->{target}'):
                    self.assertTrue(
                        states.can_transition(source, target, states.ROLE_ADMIN)
                    )

    def test_terminal_states_have_no_exit(self):
        for terminal in states.TERMINAL_STATES:
            with self.subTest(state=terminal):
                self.assertEqual(
                    states.allowed_transitions(terminal, states.ROLE_ADMIN), set()
                )

    def test_every_state_has_an_entry_timestamp_except_the_initial_one(self):
        """
        Guards the performance data: a state added without a timestamp
        would silently stop being measurable.
        """
        missing = states.ALL_STATES - set(states.ENTRY_TIMESTAMP_FIELD) - {states.NEW}
        self.assertEqual(missing, set())

    def test_timestamp_fields_all_exist_on_the_model(self):
        names = {f.name for f in Task._meta.get_fields()}
        for state, field in states.ENTRY_TIMESTAMP_FIELD.items():
            with self.subTest(state=state):
                self.assertIn(field, names)


# ===================================================================== #
# Service layer: transitions, ownership, audit
# ===================================================================== #

class TaskServiceTestCase(TestCase):

    def setUp(self):
        self.admin_user, self.admin = make_worker('admin', 'ADM', is_admin=True)
        self.alice_user, self.alice = make_worker('alice', 'W001')
        self.bob_user, self.bob = make_worker('bob', 'W002')

    def test_creating_a_task_records_an_event(self):
        task = services.create_task(actor=self.admin_user, title='New job')
        self.assertEqual(task.status, states.NEW)
        event = task.events.get()
        self.assertEqual(event.event_type, TaskEvent.TASK_CREATED)
        self.assertEqual(event.actor, self.admin_user)

    def test_claiming_sets_holder_and_timestamp_and_event(self):
        task = services.create_task(actor=self.admin_user)
        claimed = services.claim_task(task.id, self.alice, self.alice_user)
        self.assertEqual(claimed.status, states.CLAIMED)
        self.assertEqual(claimed.claimed_by, self.alice)
        self.assertIsNotNone(claimed.claimed_at)
        self.assertTrue(task.events.filter(event_type=TaskEvent.TASK_CLAIMED).exists())

    def test_a_first_claim_clears_any_training_mode_residue(self):
        """
        WORKFORCE_TRAINING_MODE (settings.py) lets a tasker save a polish/
        accuracy-check onto a task they have not claimed, so onboarding
        does not spend real Eagle minutes. This is the other half of that
        feature: the FIRST real claim on a task must never hand a genuine
        worker somebody else's rehearsal sitting in the box they are about
        to submit from.
        """
        task = services.create_task(actor=self.admin_user)
        Task.objects.filter(pk=task.id).update(
            polished_text='Une traduction de pratique.',
            accuracy_check_notes='Vérifié pour la pratique.',
        )

        claimed = services.claim_task(task.id, self.alice, self.alice_user)

        self.assertEqual(claimed.polished_text, '')
        self.assertIsNone(claimed.polished_by_id)
        self.assertIsNone(claimed.polished_at)
        self.assertEqual(claimed.accuracy_check_notes, '')
        self.assertIsNone(claimed.accuracy_check_by_id)
        self.assertIsNone(claimed.accuracy_check_at)

    def test_a_reclaim_after_release_keeps_its_real_history(self):
        """The clearing above must be scoped to a task's FIRST claim only
        -- a real worker's saved progress, surviving a release and a
        reclaim by someone else, is real work history, not a rehearsal,
        and must not be silently destroyed."""
        task = services.create_task(actor=self.admin_user)
        services.claim_task(task.id, self.alice, self.alice_user)
        Task.objects.filter(pk=task.id).update(
            polished_text='Le vrai travail d’Alice.')
        services.release_task(task.id, actor=self.alice_user)

        claimed = services.claim_task(task.id, self.bob, self.bob_user)

        self.assertEqual(claimed.polished_text, 'Le vrai travail d’Alice.')

    def test_a_second_worker_cannot_claim_a_held_task(self):
        task = services.create_task(actor=self.admin_user)
        services.claim_task(task.id, self.alice, self.alice_user)
        with self.assertRaises(services.TaskConflict):
            services.claim_task(task.id, self.bob, self.bob_user)

    def test_a_worker_cannot_claim_past_someone_elses_assignment(self):
        task = services.create_task(actor=self.admin_user)
        services.assign_task(task.id, self.alice, self.admin_user)
        with self.assertRaises(services.TaskPermissionDenied):
            services.claim_task(task.id, self.bob, self.bob_user)
        # The intended worker still can.
        self.assertEqual(
            services.claim_task(task.id, self.alice, self.alice_user).status,
            states.CLAIMED,
        )

    def test_a_suspended_worker_cannot_claim(self):
        task = services.create_task(actor=self.admin_user)
        self.alice.status = WorkerProfile.SUSPENDED
        self.alice.save(update_fields=['status'])
        with self.assertRaises(services.TaskConflict):
            services.claim_task(task.id, self.alice, self.alice_user)

    def test_the_concurrency_cap_is_enforced(self):
        """
        Six workers share one CPU-bound ASR box, so unlimited claiming
        starves everyone rather than helping the claimer.
        """
        self.alice.max_concurrent_tasks = 2
        self.alice.save(update_fields=['max_concurrent_tasks'])
        for _ in range(2):
            task = services.create_task(actor=self.admin_user)
            services.claim_task(task.id, self.alice, self.alice_user)
        third = services.create_task(actor=self.admin_user)
        with self.assertRaises(services.TaskConflict):
            services.claim_task(third.id, self.alice, self.alice_user)

    def test_releasing_returns_the_task_to_the_pool(self):
        task = services.create_task(actor=self.admin_user)
        services.claim_task(task.id, self.alice, self.alice_user)
        released = services.release_task(task.id, self.alice_user, reason='stepping away')
        self.assertEqual(released.status, states.NEW)
        self.assertIsNone(released.claimed_by)
        # And is claimable by someone else afterwards.
        self.assertEqual(
            services.claim_task(task.id, self.bob, self.bob_user).claimed_by, self.bob,
        )

    def test_an_illegal_transition_raises_and_writes_nothing(self):
        task = services.create_task(actor=self.admin_user)
        before = task.events.count()
        with self.assertRaises(states.IllegalTransition):
            services.mark_approved(task.id, self.admin_user)
        task.refresh_from_db()
        self.assertEqual(task.status, states.NEW)
        self.assertEqual(task.events.count(), before)

    def test_a_worker_cannot_approve_their_own_work(self):
        task = services.create_task(actor=self.admin_user)
        services.claim_task(task.id, self.alice, self.alice_user)
        services.start_task(task.id, self.alice_user)
        services.begin_review(task.id, self.alice_user)
        services.mark_ready(task.id, self.alice_user)
        services.mark_submitted(task.id, self.alice_user)
        with self.assertRaises(states.IllegalTransition):
            services.mark_approved(task.id, self.alice_user)
        # The admin can.
        self.assertEqual(
            services.mark_approved(task.id, self.admin_user).status, states.APPROVED,
        )

    def test_failure_preserves_diagnostics_on_the_task_and_the_event(self):
        task = services.create_task(actor=self.admin_user)
        services.claim_task(task.id, self.alice, self.alice_user)
        failed = services.mark_failed(task.id, None, reason='ffmpeg exited 1')
        self.assertEqual(failed.status, states.FAILED)
        self.assertIn('ffmpeg', failed.failure_reason)
        event = task.events.filter(event_type=TaskEvent.TASK_FAILED).get()
        self.assertFalse(event.success)
        self.assertIn('ffmpeg', event.message)

    def test_a_rejected_task_goes_back_for_revision_keeping_its_history(self):
        task = services.create_task(actor=self.admin_user)
        services.claim_task(task.id, self.alice, self.alice_user)
        services.start_task(task.id, self.alice_user)
        services.begin_review(task.id, self.alice_user)
        services.mark_ready(task.id, self.alice_user)
        services.mark_submitted(task.id, self.alice_user)
        services.mark_rejected(task.id, self.admin_user, reason='Terminology drift')
        revised = services.transition(task.id, states.IN_PROGRESS, self.alice_user)
        self.assertEqual(revised.status, states.IN_PROGRESS)
        self.assertEqual(revised.rejection_reason, 'Terminology drift')
        # Nothing was overwritten: the whole arc is still on the record.
        types = set(task.events.values_list('event_type', flat=True))
        self.assertIn(TaskEvent.REJECTED, types)
        self.assertIn(TaskEvent.SUBMISSION_COMPLETED, types)

    def test_the_full_lifecycle_writes_one_event_per_transition(self):
        task = services.create_task(actor=self.admin_user)
        services.assign_task(task.id, self.alice, self.admin_user)
        services.claim_task(task.id, self.alice, self.alice_user)
        services.start_task(task.id, self.alice_user)
        services.begin_review(task.id, self.alice_user)
        services.mark_ready(task.id, self.alice_user)
        services.mark_submitted(task.id, self.alice_user)
        services.mark_approved(task.id, self.admin_user)

        expected = [
            TaskEvent.TASK_CREATED, TaskEvent.TASK_ASSIGNED, TaskEvent.TASK_CLAIMED,
            TaskEvent.TASK_STARTED, TaskEvent.REVIEW_STARTED,
            TaskEvent.READY_FOR_SUBMISSION, TaskEvent.SUBMISSION_COMPLETED,
            TaskEvent.APPROVED,
        ]
        # Ordered by the monotonic key: insertion order is the truth for an
        # append-only log, and unlike a timestamp it cannot tie.
        actual = list(task.events.order_by('id').values_list('event_type', flat=True))
        self.assertEqual(actual, expected)

    def test_lifecycle_timestamps_are_all_populated(self):
        task = services.create_task(actor=self.admin_user)
        services.claim_task(task.id, self.alice, self.alice_user)
        services.start_task(task.id, self.alice_user)
        services.begin_review(task.id, self.alice_user)
        final = services.mark_ready(task.id, self.alice_user)
        for field in ('claimed_at', 'started_at', 'review_started_at', 'ready_at'):
            with self.subTest(field=field):
                self.assertIsNotNone(getattr(final, field))
        self.assertIsNotNone(final.processing_seconds())
        self.assertIsNotNone(final.review_seconds())


# ===================================================================== #
# Audit log integrity
# ===================================================================== #

class AuditLogTestCase(TestCase):

    def setUp(self):
        self.admin_user, self.admin = make_worker('admin', 'ADM', is_admin=True)

    def test_events_are_immutable(self):
        """
        An audit log that can be edited answers no question worth asking.
        Enforced in `save()` rather than left to convention.
        """
        task = services.create_task(actor=self.admin_user)
        event = task.events.get()
        event.message = 'tampered'
        with self.assertRaises(ValueError):
            event.save()

    def test_credentials_are_stripped_from_event_metadata(self):
        task = services.create_task(actor=self.admin_user)
        services.record_event(
            task, TaskEvent.STATUS_CHANGED, actor=self.admin_user,
            metadata={
                'password': 'hunter2', 'api_key': 'AIzaSecret',
                'Cookie': 'session=abc', 'nested': {'auth_token': 'xyz'},
                'safe_field': 'kept',
            },
        )
        stored = task.events.filter(event_type=TaskEvent.STATUS_CHANGED).get().metadata
        for key in ('password', 'api_key', 'Cookie'):
            self.assertEqual(stored[key], '***REDACTED***')
        self.assertEqual(stored['nested']['auth_token'], '***REDACTED***')
        self.assertEqual(stored['safe_field'], 'kept')

    def test_an_event_survives_deletion_of_its_actor(self):
        """History must outlive the account that made it."""
        user, profile = make_worker('temp', 'W900')
        task = services.create_task(actor=user)
        user.delete()
        event = task.events.get()
        self.assertIsNone(event.actor)
        self.assertEqual(event.event_type, TaskEvent.TASK_CREATED)


# ===================================================================== #
# Concurrency -- PostgreSQL only
# ===================================================================== #

class ConcurrentClaimTestCase(TransactionTestCase):
    """
    Two workers, one task, simultaneously.

    TransactionTestCase rather than TestCase: the threads need to see each
    other's committed rows, which a test wrapped in a single rolled-back
    transaction would prevent.
    """

    reset_sequences = True

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest(
                'select_for_update is a silent no-op on SQLite, so this '
                'test would assert a guarantee it had not exercised. '
                'Run with DB_ENGINE=postgres.'
            )
        self.admin_user, self.admin = make_worker('admin', 'ADM', is_admin=True)
        self.workers = [make_worker(f'w{i}', f'W{i:03}') for i in range(6)]

    def _claim_concurrently(self, task, participants):
        """Aligns N threads on a barrier so they contend for real."""
        barrier = threading.Barrier(len(participants))
        results, errors = [], []
        lock = threading.Lock()

        def attempt(user, profile):
            try:
                barrier.wait(timeout=10)
                claimed = services.claim_task(task.id, profile, user)
                with lock:
                    results.append(claimed.claimed_by.worker_code)
            except (services.TaskConflict, services.TaskPermissionDenied) as exc:
                with lock:
                    errors.append(exc.safe_message)
            except Exception as exc:                        # pragma: no cover
                with lock:
                    errors.append(f'{type(exc).__name__}: {exc}')
            finally:
                # Each thread opened its own connection; leaving them open
                # keeps the test database from being dropped afterwards.
                connections.close_all()

        threads = [threading.Thread(target=attempt, args=(u, p)) for u, p in participants]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        return results, errors

    def test_two_workers_claiming_the_same_task_produce_exactly_one_winner(self):
        task = make_task()
        results, errors = self._claim_concurrently(task, self.workers[:2])

        self.assertEqual(len(results), 1, f'expected 1 winner, got {results}')
        self.assertEqual(len(errors), 1, f'expected 1 refusal, got {errors}')
        self.assertIn('already been claimed', errors[0])

        task.refresh_from_db()
        self.assertEqual(task.status, states.CLAIMED)
        self.assertEqual(task.claimed_by.worker_code, results[0])

    def test_six_workers_claiming_at_once_still_produce_one_winner(self):
        """The real deployment size, contending on one task."""
        task = make_task()
        results, errors = self._claim_concurrently(task, self.workers)
        self.assertEqual(len(results), 1, f'expected 1 winner, got {results}')
        self.assertEqual(len(errors), 5)

    def test_exactly_one_claim_event_is_recorded(self):
        """
        The audit log must not show two successful claims, which is what a
        lost-update race would produce even where the final row looks right.
        """
        task = make_task()
        self._claim_concurrently(task, self.workers[:4])
        self.assertEqual(
            TaskEvent.objects.filter(task=task, event_type=TaskEvent.TASK_CLAIMED).count(),
            1,
        )

    def test_distinct_tasks_do_not_block_each_other(self):
        """
        Row locks, not table locks: two workers claiming two different
        tasks must both succeed, or the lock is a throughput disaster.
        """
        task_a, task_b = make_task(title='A'), make_task(title='B')
        barrier = threading.Barrier(2)
        outcomes = []
        lock = threading.Lock()

        def attempt(task, participant):
            user, profile = participant
            try:
                barrier.wait(timeout=10)
                services.claim_task(task.id, profile, user)
                with lock:
                    outcomes.append(True)
            except Exception:
                with lock:
                    outcomes.append(False)
            finally:
                connections.close_all()

        threads = [
            threading.Thread(target=attempt, args=(task_a, self.workers[0])),
            threading.Thread(target=attempt, args=(task_b, self.workers[1])),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(outcomes, [True, True])


# ===================================================================== #
# API: authentication
# ===================================================================== #

class AuthenticationAPITestCase(APITestCase):

    def setUp(self):
        self.admin_user, self.admin = make_worker('admin', 'ADM', is_admin=True)
        self.alice_user, self.alice = make_worker('alice', 'W001')

    def test_valid_login_returns_a_token_and_profile(self):
        response = self.client.post('/api/v1/workforce/auth/login/', {
            'username': 'alice', 'password': 'an-adequately-long-passphrase',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['token'])
        self.assertEqual(response.data['profile']['worker_code'], 'W001')
        self.assertFalse(response.data['user']['is_admin'])

    def test_invalid_password_is_rejected(self):
        response = self.client.post('/api/v1/workforce/auth/login/', {
            'username': 'alice', 'password': 'wrong',
        }, format='json')
        self.assertEqual(response.status_code, 401)

    def test_an_unknown_user_and_a_wrong_password_are_indistinguishable(self):
        """Not leaking which of six usernames exists."""
        unknown = self.client.post('/api/v1/workforce/auth/login/', {
            'username': 'nobody', 'password': 'whatever',
        }, format='json')
        wrong = self.client.post('/api/v1/workforce/auth/login/', {
            'username': 'alice', 'password': 'wrong',
        }, format='json')
        self.assertEqual(unknown.status_code, wrong.status_code)
        self.assertEqual(unknown.data['error'], wrong.data['error'])

    def test_a_suspended_worker_cannot_log_in_for_work(self):
        self.alice.status = WorkerProfile.SUSPENDED
        self.alice.save(update_fields=['status'])
        response = self.client.post('/api/v1/workforce/auth/login/', {
            'username': 'alice', 'password': 'an-adequately-long-passphrase',
        }, format='json')
        self.assertEqual(response.status_code, 403)

    def test_a_disabled_django_account_cannot_log_in(self):
        self.alice_user.is_active = False
        self.alice_user.save(update_fields=['is_active'])
        response = self.client.post('/api/v1/workforce/auth/login/', {
            'username': 'alice', 'password': 'an-adequately-long-passphrase',
        }, format='json')
        self.assertEqual(response.status_code, 401)

    def test_anonymous_access_is_refused_everywhere(self):
        for url in ('/api/v1/workforce/tasks/', '/api/v1/workforce/me/',
                    '/api/v1/workforce/workers/', '/api/v1/workforce/stats/'):
            with self.subTest(url=url):
                self.assertIn(self.client.get(url).status_code, (401, 403))

    def test_logout_invalidates_the_token(self):
        login = self.client.post('/api/v1/workforce/auth/login/', {
            'username': 'alice', 'password': 'an-adequately-long-passphrase',
        }, format='json')
        token = login.data['token']
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(client.get('/api/v1/workforce/me/').status_code, 200)
        client.post('/api/v1/workforce/auth/logout/')
        self.assertIn(client.get('/api/v1/workforce/me/').status_code, (401, 403))

    def _logged_in_client(self):
        login = self.client.post('/api/v1/workforce/auth/login/', {
            'username': 'alice', 'password': 'an-adequately-long-passphrase',
        }, format='json')
        token = login.data['token']
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        return client, token

    def test_a_fresh_token_is_not_idle_expired(self):
        client, _ = self._logged_in_client()
        self.assertEqual(client.get('/api/v1/workforce/me/').status_code, 200)

    def test_an_idle_token_is_rejected_and_deleted(self):
        """
        THE BUG THIS CLOSES. DRF's base Token has no expiry at all --
        confirmed live, a worker stayed signed in for two straight weeks.
        Deleted, not merely refused: a refused-but-still-valid token would
        keep coming back on every retry with the same stale timestamp.
        """
        from django.utils import timezone
        from datetime import timedelta
        from rest_framework.authtoken.models import Token
        from apps.workforce.models import TokenActivity

        client, key = self._logged_in_client()
        token = Token.objects.get(key=key)
        TokenActivity.objects.filter(token=token).update(
            last_used_at=timezone.now() - timedelta(hours=9))

        response = client.get('/api/v1/workforce/me/')
        self.assertIn(response.status_code, (401, 403))
        self.assertFalse(Token.objects.filter(key=key).exists())

    def test_activity_inside_the_window_keeps_the_token_alive_and_slides_it_forward(self):
        from django.utils import timezone
        from datetime import timedelta
        from rest_framework.authtoken.models import Token
        from apps.workforce.models import TokenActivity

        client, key = self._logged_in_client()
        token = Token.objects.get(key=key)
        backdated = timezone.now() - timedelta(hours=7)
        TokenActivity.objects.filter(token=token).update(last_used_at=backdated)

        response = client.get('/api/v1/workforce/me/')
        self.assertEqual(response.status_code, 200)
        # SLIDING, NOT FIXED. Activity just now must have moved the clock
        # forward -- an idle window measured from the ORIGINAL login rather
        # than from last use would still be an absolute timeout wearing an
        # idle timeout's name.
        activity = TokenActivity.objects.get(token=token)
        self.assertGreater(activity.last_used_at, backdated)

    def test_signing_in_again_resets_a_reused_tokens_idle_clock(self):
        """get_or_create hands back the SAME token on a second login when
        the first has not yet gone idle -- signing in is activity too, and
        must reset the clock rather than leaving the stale timestamp from
        before this login."""
        from django.utils import timezone
        from datetime import timedelta
        from rest_framework.authtoken.models import Token
        from apps.workforce.models import TokenActivity

        client, key = self._logged_in_client()
        token = Token.objects.get(key=key)
        backdated = timezone.now() - timedelta(hours=7, minutes=55)
        TokenActivity.objects.filter(token=token).update(last_used_at=backdated)

        second_login = self.client.post('/api/v1/workforce/auth/login/', {
            'username': 'alice', 'password': 'an-adequately-long-passphrase',
        }, format='json')
        self.assertEqual(second_login.data['token'], key)  # same token, reused

        activity = TokenActivity.objects.get(token=token)
        self.assertGreater(activity.last_used_at, backdated)

    def test_weak_passwords_are_refused_when_creating_a_worker(self):
        """
        AUTH_PASSWORD_VALIDATORS was an empty list before Phase 2, which
        would have allowed "1234" for a worker account.
        """
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post('/api/v1/workforce/workers/', {
            'username': 'weak', 'password': '1234', 'worker_code': 'W777',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('password', response.data)


# ===================================================================== #
# API: authorization
# ===================================================================== #

class TaskAuthorizationAPITestCase(APITestCase):

    def setUp(self):
        self.admin_user, self.admin = make_worker('admin', 'ADM', is_admin=True)
        self.alice_user, self.alice = make_worker('alice', 'W001')
        self.bob_user, self.bob = make_worker('bob', 'W002')

        self.alice_task = services.create_task(actor=self.admin_user, title="Alice's")
        services.claim_task(self.alice_task.id, self.alice, self.alice_user)
        self.open_task = services.create_task(actor=self.admin_user, title='Open')

    def as_alice(self):
        self.client.force_authenticate(user=self.alice_user)

    def as_bob(self):
        self.client.force_authenticate(user=self.bob_user)

    def as_admin(self):
        self.client.force_authenticate(user=self.admin_user)

    def _task_ids(self):
        """Handles both paginated and unpaginated list responses, so the
        test does not break the day pagination is switched on."""
        data = self.client.get('/api/v1/workforce/tasks/').data
        rows = data['results'] if isinstance(data, dict) else data
        return {row['id'] for row in rows}

    def test_a_worker_cannot_read_another_workers_task(self):
        self.as_bob()
        response = self.client.get(f'/api/v1/workforce/tasks/{self.alice_task.id}/')
        self.assertIn(response.status_code, (403, 404))

    def test_a_worker_cannot_act_on_another_workers_task(self):
        self.as_bob()
        for verb in ('start', 'release', 'begin-review', 'ready', 'submit'):
            with self.subTest(action=verb):
                response = self.client.post(
                    f'/api/v1/workforce/tasks/{self.alice_task.id}/{verb}/'
                )
                self.assertIn(response.status_code, (403, 404))

    def test_a_worker_list_excludes_other_workers_tasks(self):
        self.as_bob()
        ids = self._task_ids()
        self.assertNotIn(str(self.alice_task.id), ids)
        self.assertIn(str(self.open_task.id), ids)

    def test_a_worker_cannot_reach_admin_endpoints(self):
        self.as_alice()
        self.assertEqual(self.client.get('/api/v1/workforce/workers/').status_code, 403)
        self.assertEqual(self.client.get('/api/v1/workforce/stats/').status_code, 403)
        self.assertEqual(
            self.client.post('/api/v1/workforce/tasks/', {'title': 'x'}, format='json').status_code,
            403,
        )

    def test_a_worker_cannot_approve_or_reject(self):
        self.as_alice()
        for verb in ('approve', 'reject'):
            with self.subTest(action=verb):
                response = self.client.post(
                    f'/api/v1/workforce/tasks/{self.alice_task.id}/{verb}/',
                    {'reason': 'x'}, format='json',
                )
                self.assertEqual(response.status_code, 403)

    def test_an_admin_sees_every_task(self):
        self.as_admin()
        ids = self._task_ids()
        self.assertIn(str(self.alice_task.id), ids)
        self.assertIn(str(self.open_task.id), ids)

    def test_the_acting_worker_comes_from_the_session_not_the_body(self):
        """
        THE RULE THE WHOLE PERMISSION MODEL RESTS ON. A body naming another
        worker must change nothing.
        """
        self.as_bob()
        response = self.client.post(
            f'/api/v1/workforce/tasks/{self.open_task.id}/claim/',
            {'worker': str(self.alice.id), 'worker_id': str(self.alice.id)},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.open_task.refresh_from_db()
        self.assertEqual(self.open_task.claimed_by, self.bob)

    def test_status_cannot_be_changed_by_patching_the_task(self):
        """State moves only through the action endpoints, which hold the
        lock and write the audit event."""
        self.as_alice()
        response = self.client.patch(
            f'/api/v1/workforce/tasks/{self.alice_task.id}/',
            {'status': states.APPROVED}, format='json',
        )
        self.assertEqual(response.status_code, 405)
        self.alice_task.refresh_from_db()
        self.assertEqual(self.alice_task.status, states.CLAIMED)


# ===================================================================== #
# API: workflow and admin
# ===================================================================== #

class _AcceptingPlatform:
    """
    A platform that says yes, with minutes to spare.

    Deliberately not a subclass of EagleIntegrationService: this stands in
    for a configured, healthy Eagle, and inheriting the inert base's
    "raise EagleNotConfigured" defaults would defeat the point.
    """

    def is_configured(self):
        return True

    def check_session(self, product):
        return {'alive': True, 'minutes_balance': 9999}

    def minutes_balance(self, product):
        return 9999

    def claim_task(self, external_id, source=None, **extra):
        return {'accepted': True}


class TaskWorkflowAPITestCase(APITestCase):

    def setUp(self):
        self.admin_user, self.admin = make_worker('admin', 'ADM', is_admin=True)
        self.alice_user, self.alice = make_worker('alice', 'W001')

    def test_an_admin_can_create_assign_and_an_worker_can_work_it_through(self):
        """
        CLAIMING NOW TALKS TO THE PLATFORM, so this walkthrough needs one.

        The task carries an external id, and a task with an external id is
        one the platform owns: claiming it here without telling Eagle would
        mark it as this worker's while leaving it open for anyone else to
        take. The endpoint therefore goes through `eagle_claim`, and this
        test supplies a platform that accepts -- which means the walkthrough
        now also covers the balance check and the external call, not just
        the state machine.
        """
        self.enterContext(mock.patch(
            'apps.workforce.eagle.get_eagle_service',
            return_value=_AcceptingPlatform()))

        self.client.force_authenticate(user=self.admin_user)
        created = self.client.post('/api/v1/workforce/tasks/', {
            'title': 'Interview 4244', 'kind': 'transcription',
            'external_task_id': '4244', 'external_source': 'eagle_transcribe',
        }, format='json')
        self.assertEqual(created.status_code, 201)
        task_id = created.data['id']

        assigned = self.client.post(
            f'/api/v1/workforce/tasks/{task_id}/assign/',
            {'worker': str(self.alice.id)}, format='json',
        )
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.data['status'], states.ASSIGNED)

        self.client.force_authenticate(user=self.alice_user)
        for verb, expected in [
            ('claim', states.CLAIMED),
            ('start', states.IN_PROGRESS),
            ('begin-review', states.REVIEW),
            ('ready', states.READY_TO_SUBMIT),
            ('submit', states.SUBMITTED),
        ]:
            with self.subTest(action=verb):
                response = self.client.post(f'/api/v1/workforce/tasks/{task_id}/{verb}/')
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['status'], expected)

        self.client.force_authenticate(user=self.admin_user)
        approved = self.client.post(f'/api/v1/workforce/tasks/{task_id}/approve/')
        self.assertEqual(approved.data['status'], states.APPROVED)

    def test_claiming_an_already_claimed_task_returns_409(self):
        _, bob = make_worker('bob', 'W002')
        task = services.create_task(actor=self.admin_user)
        services.claim_task(task.id, bob, bob.user)
        self.client.force_authenticate(user=self.alice_user)
        response = self.client.post(f'/api/v1/workforce/tasks/{task.id}/claim/')
        self.assertEqual(response.status_code, 409)

    def test_an_illegal_transition_returns_400(self):
        """
        The holder attempting a move the machine does not define. Must be
        400, distinct from the 403 a NON-holder gets -- permission is
        checked before the state machine, which is the right order.
        """
        task = services.create_task(actor=self.admin_user)
        services.claim_task(task.id, self.alice, self.alice_user)
        self.client.force_authenticate(user=self.alice_user)
        # CLAIMED -> SUBMITTED is not a defined transition.
        response = self.client.post(f'/api/v1/workforce/tasks/{task.id}/submit/')
        self.assertEqual(response.status_code, 400)

    def test_a_non_holder_is_refused_before_the_state_machine(self):
        task = services.create_task(actor=self.admin_user)
        services.claim_task(task.id, self.alice, self.alice_user)
        _, mallory = make_worker('mallory', 'W003')
        self.client.force_authenticate(user=mallory.user)
        response = self.client.post(f'/api/v1/workforce/tasks/{task.id}/submit/')
        self.assertIn(response.status_code, (403, 404))

    def test_rejection_requires_a_reason(self):
        task = services.create_task(actor=self.admin_user)
        services.claim_task(task.id, self.alice, self.alice_user)
        services.start_task(task.id, self.alice_user)
        services.begin_review(task.id, self.alice_user)
        services.mark_ready(task.id, self.alice_user)
        services.mark_submitted(task.id, self.alice_user)
        self.client.force_authenticate(user=self.admin_user)
        self.assertEqual(
            self.client.post(f'/api/v1/workforce/tasks/{task.id}/reject/').status_code,
            400,
        )

    def test_available_transitions_reflect_the_callers_role(self):
        task = services.create_task(actor=self.admin_user)
        services.claim_task(task.id, self.alice, self.alice_user)
        services.start_task(task.id, self.alice_user)
        services.begin_review(task.id, self.alice_user)
        services.mark_ready(task.id, self.alice_user)
        services.mark_submitted(task.id, self.alice_user)

        self.client.force_authenticate(user=self.alice_user)
        worker_view = self.client.get(f'/api/v1/workforce/tasks/{task.id}/')
        self.assertNotIn(states.APPROVED, worker_view.data['available_transitions'])

        self.client.force_authenticate(user=self.admin_user)
        admin_view = self.client.get(f'/api/v1/workforce/tasks/{task.id}/')
        self.assertIn(states.APPROVED, admin_view.data['available_transitions'])

    def test_a_worker_can_read_their_own_task_history(self):
        task = services.create_task(actor=self.admin_user)
        services.claim_task(task.id, self.alice, self.alice_user)
        self.client.force_authenticate(user=self.alice_user)
        response = self.client.get(f'/api/v1/workforce/tasks/{task.id}/events/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(TaskEvent.TASK_CLAIMED,
                      {row['event_type'] for row in response.data})

    def test_deactivating_a_worker_releases_their_held_tasks(self):
        """Otherwise a suspended worker's queue becomes unreachable work."""
        task = services.create_task(actor=self.admin_user)
        services.claim_task(task.id, self.alice, self.alice_user)
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(f'/api/v1/workforce/workers/{self.alice.id}/deactivate/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['released_tasks'], 1)
        task.refresh_from_db()
        self.assertEqual(task.status, states.NEW)
        self.assertIsNone(task.claimed_by)

    def test_admin_stats_report_counts(self):
        services.create_task(actor=self.admin_user)
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/v1/workforce/stats/')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.data['totals']['tasks'], 1)
        self.assertIn('workers', response.data)


class QueueOrderingAgreesWithIsReadyTestCase(APITestCase):
    """
    The queue's "ready work first" ordering used to treat a completed
    transcript job as "ready" on its own -- the same looser definition the
    warm pool's `run_cycle` count used before that was fixed. For a
    translation task that is wrong: `TaskSerializer.get_is_ready` (and
    `warmpool.is_warm`) also require a deliverable_revision.

    A transcript-only translation sorted into the front-page "ready" group
    pushes a genuinely ready task onto a later page. A tasker (or the
    dashboard's per-page ready count) never sees the disagreement directly --
    it just reads as "ready now" undercounting the site-wide ready total.
    """

    def setUp(self):
        self.admin_user, self.admin = make_worker('admin', 'ADM', is_admin=True)
        self.worker_user, self.worker = make_worker('carol', 'W004')
        self.client.force_authenticate(user=self.worker_user)

    def _translation_task(self, external_id, *, translated):
        from apps.transcriptions.models import AIRevision, Project, TranscriptionJob

        project = Project.objects.create(name=f'queue-order-{external_id}')
        job = TranscriptionJob.objects.create(
            project=project, input_source='upload', status='completed')
        task = make_task(
            kind=Task.TRANSLATION, external_source='translation',
            external_task_id=external_id, status=states.NEW,
            duration_seconds=600,
        )
        Task.objects.filter(pk=task.pk).update(job=job)
        if translated:
            revision = AIRevision.objects.create(
                job=job, operation='translate', target_language='fr',
                status='completed')
            Task.objects.filter(pk=task.pk).update(deliverable_revision=revision)
        task.refresh_from_db()
        return task

    def test_a_transcript_only_translation_does_not_outrank_a_translated_one(self):
        # Created first (and shorter), so the old ordering -- which only
        # looked at job__status -- would have sorted this one first.
        stuck = self._translation_task('q-stuck', translated=False)
        stuck.duration_seconds = 60
        stuck.save(update_fields=['duration_seconds'])

        ready = self._translation_task('q-ready', translated=True)

        response = self.client.get(
            '/api/v1/workforce/tasks/?kind=translation&available=true')
        self.assertEqual(response.status_code, 200)
        ids_in_order = [row['id'] for row in response.data]
        self.assertLess(
            ids_in_order.index(str(ready.id)), ids_in_order.index(str(stuck.id)),
            'a genuinely ready translation must sort ahead of a '
            'transcript-only one, regardless of duration',
        )
        by_id = {row['id']: row for row in response.data}
        self.assertTrue(by_id[str(ready.id)]['is_ready'])
        self.assertFalse(by_id[str(stuck.id)]['is_ready'])

    def test_ready_count_on_a_short_page_matches_is_ready_not_job_status(self):
        """
        The regression as it actually appeared: with several transcript-only
        translations mixed in, a short first page should still show every
        readied translation as ready -- not just the ones whose transcript
        happened to finish first.
        """
        stuck_tasks = [
            self._translation_task(f'q-stuck-{i}', translated=False)
            for i in range(3)
        ]
        for t in stuck_tasks:
            t.duration_seconds = 30
            t.save(update_fields=['duration_seconds'])
        ready = self._translation_task('q-ready-2', translated=True)

        response = self.client.get(
            '/api/v1/workforce/tasks/'
            '?kind=translation&available=true&page=1&page_size=2')
        self.assertEqual(response.status_code, 200)
        page_ids = [row['id'] for row in response.data['results']]
        self.assertIn(
            str(ready.id), page_ids,
            'the one truly ready task must not be crowded off the first '
            'page by transcript-only tasks the old ordering treated as '
            'equally ready',
        )

    def test_the_envelope_ready_count_is_the_whole_filter_not_the_page(self):
        """
        The bug the frontend hit: "ready now" was counted over the loaded
        page, so it could never exceed page_size however many were actually
        ready. The server now returns ready_count over the full filtered
        queryset, and it must not follow the page slice.
        """
        ready = [
            self._translation_task(f'q-r-{i}', translated=True) for i in range(4)
        ]
        self._translation_task('q-s', translated=False)  # not ready, excluded

        response = self.client.get(
            '/api/v1/workforce/tasks/'
            '?kind=translation&available=true&page=1&page_size=2')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 2)      # page is short
        self.assertEqual(response.data['ready_count'], len(ready))  # count is not

    def test_work_status_ready_now_excludes_a_transcript_only_translation(self):
        self._translation_task('ws-ready', translated=True)
        self._translation_task('ws-stuck', translated=False)

        response = self.client.get('/api/v1/workforce/work/status/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data['ready_now'], 1,
            'a translation whose translate pass never produced a revision is '
            'not something a tasker can start, even with a completed transcript',
        )


# ===================================================================== #
# Engine boundary
# ===================================================================== #

class EngineBoundaryTestCase(TestCase):
    """
    The workforce layer wraps the engine; it must not have grown into it.
    """

    def test_a_task_references_the_engine_rather_than_copying_it(self):
        from apps.transcriptions.models import TranscriptionJob, Project
        project = Project.objects.create(name='P')
        job = TranscriptionJob.objects.create(
            project=project, input_source='url', source_url='https://e.com/a.mp3',
        )
        task = make_task(job=job)
        self.assertEqual(task.job, job)
        # No transcript text is duplicated onto the task.
        field_names = {f.name for f in Task._meta.get_fields()}
        for forbidden in ('text', 'transcript', 'segments', 'content'):
            self.assertNotIn(forbidden, field_names)

    def test_deleting_a_job_keeps_the_operational_record(self):
        from apps.transcriptions.models import TranscriptionJob, Project
        project = Project.objects.create(name='P')
        job = TranscriptionJob.objects.create(
            project=project, input_source='url', source_url='https://e.com/a.mp3',
        )
        task = make_task(job=job)
        job.delete()
        task.refresh_from_db()
        self.assertIsNone(task.job)

    def test_the_eagle_interface_is_inert_and_leaks_no_implementation(self):
        """
        Phase 4's seam. The default must FAIL rather than silently no-op,
        and importing it must not drag in Playwright or an HTTP client.
        """
        from .eagle import (
            EagleIntegrationError, EagleIntegrationService, EagleNotConfigured,
            get_eagle_service,
        )

        # THE BASE IS STILL INERT. Phase 4 registered a real implementation,
        # so the accessor no longer returns this -- but the null object it
        # falls back to when that import fails must still refuse every call
        # rather than quietly returning nothing, which would look like an
        # Eagle with no work in it.
        inert = EagleIntegrationService()
        self.assertFalse(inert.is_configured())
        for call in (
            lambda: inert.get_task('1'),
            lambda: inert.get_task_media('1'),
            lambda: inert.get_authenticated_media('1'),
            lambda: inert.submit_task('1', 'content'),
            lambda: inert.get_submission_status('1'),
        ):
            with self.assertRaises(EagleNotConfigured):
                call()

        # AND THE REGISTERED SERVICE FAILS CLOSED TOO. A nonsense product
        # must raise rather than reach the network or return an empty
        # result; both alternatives read to a caller as "no tasks today".
        with self.assertRaises(EagleIntegrationError):
            get_eagle_service().get_task('1')

        # Scanned with ast rather than by substring: the module docstring
        # NAMES playwright and curl_cffi while explaining why it must not
        # import them, and a text search cannot tell prose from a dependency.
        import ast
        import inspect
        from . import eagle

        tree = ast.parse(inspect.getsource(eagle))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
        for forbidden in ('playwright', 'curl_cffi', 'requests', 'httpx',
                          'selenium', 'urllib'):
            self.assertNotIn(forbidden, imported)
