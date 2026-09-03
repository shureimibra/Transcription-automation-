"""
Account administration, and the one question it has to answer truthfully:
when you remove somebody, are they actually out?

WHY THIS IS TESTED THROUGH THE LOGIN ENDPOINT. Asserting that a flag was
written proves nothing about whether the person can still work. There are
TWO gates -- the Django account and the worker profile -- and closing one
while believing both are shut is exactly the failure that matters: the
operator thinks the account is gone and the person signs in the next
morning. So every test here ends at the door, not at the database.
"""
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.test import APIClient

from . import states
from .models import Task, WorkerProfile
from .tests import make_task, make_worker


class WorkerAdminTestCase(TestCase):

    LOGIN = '/api/v1/workforce/auth/login/'

    def setUp(self):
        super().setUp()
        # Disabling releases held work, and releasing is an administrator's
        # act -- so the tests need one to exist, exactly as a real
        # deployment does.
        self.boss, _ = make_worker('boss', 'ADMIN')
        self.boss.is_staff = self.boss.is_superuser = True
        self.boss.save()

        self.user, self.worker = make_worker('ana', 'W-01')
        self.user.set_password('first-password-2026')
        self.user.save()
        self.client = APIClient()

    def _can_log_in(self, password='first-password-2026'):
        response = self.client.post(
            self.LOGIN, {'username': 'ana', 'password': password})
        return response.status_code == 200

    # ------------------------------------------------------------------ #
    # Passwords
    # ------------------------------------------------------------------ #

    def test_a_reset_password_works_and_the_old_one_stops(self):
        self.assertTrue(self._can_log_in())

        with mock.patch('apps.workforce.management.commands.worker.getpass',
                        return_value='second-password-2026'):
            call_command('worker', 'password', 'ana')

        self.assertFalse(self._can_log_in('first-password-2026'))
        self.assertTrue(self._can_log_in('second-password-2026'))

    def test_a_mistyped_confirmation_changes_nothing(self):
        """The operator is typing blind -- getpass shows nothing -- so a slip
        must leave the account exactly as it was, not half-reset."""
        with mock.patch('apps.workforce.management.commands.worker.getpass',
                        side_effect=['aaaa-2026-aaaa', 'bbbb-2026-bbbb']):
            with self.assertRaises(CommandError):
                call_command('worker', 'password', 'ana')

        self.assertTrue(self._can_log_in('first-password-2026'))

    def test_a_weak_password_is_refused(self):
        with mock.patch('apps.workforce.management.commands.worker.getpass',
                        return_value='password'):
            with self.assertRaises(CommandError):
                call_command('worker', 'password', 'ana')

        self.assertTrue(self._can_log_in('first-password-2026'))

    # ------------------------------------------------------------------ #
    # Disabling, which is the question that was actually asked
    # ------------------------------------------------------------------ #

    def test_a_disabled_worker_cannot_log_in_at_all(self):
        call_command('worker', 'disable', 'ana')

        self.assertFalse(self._can_log_in())

    def test_disabling_closes_BOTH_gates(self):
        """
        Either gate alone stops work, so closing one would look like success
        while leaving the other open -- and the account would come back the
        moment somebody reactivated the half of it they knew about.
        """
        call_command('worker', 'disable', 'ana')

        self.user.refresh_from_db()
        self.worker.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.worker.status, WorkerProfile.SUSPENDED)
        self.assertFalse(self.worker.is_active_worker)

    def test_enabling_lets_them_straight_back_in(self):
        call_command('worker', 'disable', 'ana')
        call_command('worker', 'enable', 'ana')

        self.assertTrue(self._can_log_in())

    def test_the_password_survives_a_disable_and_enable(self):
        # Locking somebody out for a week must not silently require a new
        # password when they return.
        call_command('worker', 'disable', 'ana')
        call_command('worker', 'enable', 'ana')

        self.assertTrue(self._can_log_in('first-password-2026'))

    def test_work_in_hand_goes_back_to_the_queue(self):
        """
        HOLDING ON TO IT IS WHAT LOSES IT. A suspended worker's claimed tasks
        are reachable by nobody: the one-hour clock runs out and the work
        goes, along with the minutes it was bought with. Releasing puts each
        one back where somebody else can still finish it.

        This test exists because the command was first written the other
        way round -- keeping the tasks and printing a warning -- which read
        as the careful choice and was the destructive one. The `deactivate`
        endpoint had always released them; the two surfaces must not
        disagree about what suspending someone means.
        """
        task = make_task(status=states.CLAIMED, claimed_by=self.worker)

        out = self._run('disable', 'ana')

        task.refresh_from_db()
        self.assertIsNone(task.claimed_by)
        self.assertIn(task.status, states.CLAIMABLE_STATES)
        self.assertIn('Released 1 task', out)

    def test_disabling_matches_the_deactivate_endpoint(self):
        """One meaning of "suspend", whichever surface performs it."""
        from apps.workforce import services

        task = make_task(status=states.CLAIMED, claimed_by=self.worker)
        call_command('worker', 'disable', 'ana')
        task.refresh_from_db()
        cli_status, cli_holder = task.status, task.claimed_by

        other_user, other = make_worker('bo2', 'W-03')
        other_task = make_task(external_task_id='9002',
                               status=states.CLAIMED, claimed_by=other)
        other.status = WorkerProfile.SUSPENDED
        other.save(update_fields=['status'])
        # The endpoint passes the signed-in administrator; mirroring it
        # means passing one here too, not None.
        services.release_task(other_task.id, actor=self.boss,
                              reason='Worker deactivated.')
        other_task.refresh_from_db()

        self.assertEqual(cli_status, other_task.status)
        self.assertEqual(cli_holder, other_task.claimed_by)

    def test_an_unknown_username_says_so_plainly(self):
        # An operator at their own console, not a login form: hiding which
        # accounts exist here only makes their job harder.
        with self.assertRaises(CommandError) as caught:
            call_command('worker', 'disable', 'nobody')

        self.assertIn('nobody', str(caught.exception))

    def test_an_action_without_a_username_refuses_rather_than_guessing(self):
        with self.assertRaises(CommandError):
            call_command('worker', 'disable')

    # ------------------------------------------------------------------ #

    def test_the_listing_answers_can_this_person_work(self):
        """One column, not two flags for the reader to combine themselves."""
        make_worker('bo', 'W-02')
        call_command('worker', 'disable', 'bo')

        out = self._run('list')

        self.assertIn('CAN WORK', out)
        for line in out.splitlines():
            if line.startswith('bo '):
                self.assertIn('NO', line)
            if line.startswith('ana '):
                self.assertIn('yes', line)

    # ------------------------------------------------------------------ #
    # The admin PAGE, which is where this is actually done from
    # ------------------------------------------------------------------ #

    def test_an_admin_can_reset_a_password_from_the_page(self):
        """
        The management command needs SSH. "I forgot my password" is the
        commonest administrative request there is, and it should not require
        a terminal -- least of all from the phone this is usually read on.
        """
        admin_user = self.boss
        account = self._account_for(self.user)

        self.client.force_authenticate(admin_user)
        response = self.client.post(
            f'/api/v1/workforce/accounts/{account.id}/set-password/',
            {'password': 'brand-new-2026-pass'})

        self.assertEqual(response.status_code, 200)
        self.client.force_authenticate(None)
        self.assertTrue(self._can_log_in('brand-new-2026-pass'))
        self.assertFalse(self._can_log_in('first-password-2026'))

    def test_a_tasker_cannot_reset_anybody_password(self):
        """Including their own — this is the door to every account."""
        account = self._account_for(self.user)

        self.client.force_authenticate(self.user)
        response = self.client.post(
            f'/api/v1/workforce/accounts/{account.id}/set-password/',
            {'password': 'not-allowed-2026'})

        self.assertIn(response.status_code, (401, 403))
        self.client.force_authenticate(None)
        self.assertTrue(self._can_log_in('first-password-2026'))

    def test_a_weak_password_is_refused_by_the_endpoint_too(self):
        admin_user = self.boss
        account = self._account_for(self.user)

        self.client.force_authenticate(admin_user)
        response = self.client.post(
            f'/api/v1/workforce/accounts/{account.id}/set-password/',
            {'password': 'password'})

        self.assertEqual(response.status_code, 400)
        self.client.force_authenticate(None)
        self.assertTrue(self._can_log_in('first-password-2026'))

    @staticmethod
    def _account_for(user):
        from .models import Account
        return Account.objects.get(user=user)

    @staticmethod
    def _run(*args):
        from io import StringIO
        buffer = StringIO()
        call_command('worker', *args, stdout=buffer)
        return buffer.getvalue()
