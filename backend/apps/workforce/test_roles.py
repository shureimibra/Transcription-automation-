"""
Tests for the account role model.

THE CLAIM WORTH TESTING is that role is STORED and AUTHORITATIVE, not
inferred. The old inference had two failure modes this file pins shut:

  * a tool-only user could not exist at all -- no profile meant no login;
  * suspending a tasker would have flipped them to "not a worker", which
    under any inference scheme reads as "tool user", handing a suspended
    person the studio. Suspension is a STATUS; role is separate.

The capability table is tested directly rather than through endpoints,
because it is a table: asserting against it is cheaper and more complete
than driving every view for every role.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APITestCase

from . import roles, states
from .models import Account, Task, WorkerProfile
from .permissions import account_of, account_role, is_admin, is_tasker, is_tool_user
from .tests import make_task, make_worker

PW = 'an-adequately-long-passphrase'


def make_account(username, role, **kwargs):
    user = User.objects.create_user(
        username=username, password=PW,
        is_staff=(role == roles.ADMIN),
    )
    account = Account.objects.create(user=user, role=role)
    profile = None
    if role == roles.TASKER:
        profile = WorkerProfile.objects.create(
            user=user, worker_code=kwargs.get('code', username.upper()),
            display_name=username.title(),
            status=kwargs.get('status', WorkerProfile.ACTIVE),
        )
    return user, account, profile


# ===================================================================== #
# The capability table
# ===================================================================== #

class CapabilityTableTestCase(TestCase):

    def test_each_role_reaches_exactly_what_it_should(self):
        self.assertEqual(
            roles.capabilities(roles.ADMIN),
            frozenset({roles.WORKFORCE, roles.ADMINISTRATION, roles.STUDIO}),
        )
        self.assertEqual(
            roles.capabilities(roles.TASKER),
            frozenset({roles.WORKFORCE, roles.STUDIO, roles.MEASURED}),
        )
        self.assertEqual(roles.capabilities(roles.TOOL), frozenset({roles.STUDIO}))

    def test_a_tool_user_reaches_neither_the_workforce_nor_administration(self):
        self.assertFalse(roles.can(roles.TOOL, roles.WORKFORCE))
        self.assertFalse(roles.can(roles.TOOL, roles.ADMINISTRATION))
        self.assertTrue(roles.can(roles.TOOL, roles.STUDIO))

    def test_a_tasker_is_not_an_administrator(self):
        self.assertFalse(roles.can(roles.TASKER, roles.ADMINISTRATION))

    def test_only_taskers_are_measured(self):
        # An administrator appearing in the throughput report as a worker
        # with no submissions would be a misleading statistic.
        self.assertTrue(roles.can(roles.TASKER, roles.MEASURED))
        self.assertFalse(roles.can(roles.ADMIN, roles.MEASURED))
        self.assertFalse(roles.can(roles.TOOL, roles.MEASURED))

    def test_an_unknown_role_gets_nothing(self):
        # Fails closed: a value from an older version, or a typo, must not
        # default into something permissive.
        self.assertEqual(roles.capabilities('supervisor'), frozenset())
        self.assertFalse(roles.can('supervisor', roles.STUDIO))
        self.assertFalse(roles.can(None, roles.STUDIO))

    def test_the_default_role_is_the_least_privileged(self):
        self.assertEqual(roles.DEFAULT_ROLE, roles.TOOL)
        self.assertEqual(roles.capabilities(roles.DEFAULT_ROLE), frozenset({roles.STUDIO}))

    def test_an_unknown_role_keeps_its_raw_name(self):
        self.assertEqual(roles.label('supervisor'), 'supervisor')
        self.assertEqual(roles.label(roles.TASKER), 'Tasker')


# ===================================================================== #
# Role is stored, not inferred
# ===================================================================== #

class StoredRoleTestCase(TestCase):

    def test_a_suspended_tasker_is_still_a_tasker(self):
        """
        The bug the stored role exists to prevent.

        Under inference, "no active WorkerProfile" reads as "not a worker",
        which collapses to "tool user" -- so suspending someone would hand
        them the studio instead of taking access away.
        """
        user, _, profile = make_account('zed', roles.TASKER, status=WorkerProfile.SUSPENDED)

        self.assertEqual(account_role(user), roles.TASKER)
        self.assertTrue(is_tasker(user))
        self.assertFalse(is_tool_user(user))
        self.assertFalse(profile.is_active_worker)

    def test_a_disabled_django_account_does_not_change_the_role(self):
        user, _, _ = make_account('ana', roles.TASKER, code='W-01')
        user.is_active = False
        user.save(update_fields=['is_active'])
        user.refresh_from_db()

        self.assertEqual(Account.objects.get(user=user).role, roles.TASKER)

    def test_an_account_is_created_on_first_sight_for_a_user_that_lacks_one(self):
        # Users arrive from outside this app -- createsuperuser, fixtures, a
        # future SSO. A missing role row must not 500 the API.
        user = User.objects.create_user(username='stray', password=PW)
        Account.objects.filter(user=user).delete()

        account = account_of(user)

        self.assertIsNotNone(account)
        self.assertEqual(account.role, roles.TOOL)

    def test_a_staff_user_without_a_role_row_is_treated_as_an_administrator(self):
        # Otherwise `createsuperuser` produces someone locked out of the
        # admin surface of their own application.
        user = User.objects.create_user(username='root', password=PW, is_staff=True)
        Account.objects.filter(user=user).delete()

        self.assertEqual(account_of(user).role, roles.ADMIN)
        self.assertTrue(is_admin(user))

    def test_role_and_the_staff_flag_agree_for_created_workers(self):
        _, account, _ = make_account('bo', roles.TASKER, code='W-02')
        self.assertEqual(account.role, roles.TASKER)
        self.assertFalse(account.user.is_staff)


# ===================================================================== #
# Signing in
# ===================================================================== #

class RoleLoginTestCase(APITestCase):

    def setUp(self):
        self.url = '/api/v1/workforce/auth/login/'

    def _login(self, username):
        return self.client.post(
            self.url, {'username': username, 'password': PW}, format='json',
        )

    def test_a_tool_user_can_sign_in(self):
        """The requirement that was impossible before this model existed."""
        make_account('toolonly', roles.TOOL)

        response = self._login('toolonly')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['user']['role'], roles.TOOL)
        self.assertEqual(response.data['capabilities'], ['studio'])
        self.assertIsNone(response.data['profile'])
        self.assertFalse(response.data['user']['is_admin'])

    def test_a_tasker_signs_in_with_their_profile(self):
        make_account('ana', roles.TASKER, code='W-01')

        response = self._login('ana')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['user']['role'], roles.TASKER)
        self.assertEqual(response.data['profile']['worker_code'], 'W-01')
        self.assertIn('workforce', response.data['capabilities'])

    def test_an_administrator_signs_in_with_administration(self):
        make_account('boss', roles.ADMIN)

        response = self._login('boss')

        self.assertEqual(response.status_code, 200)
        self.assertIn('administration', response.data['capabilities'])
        self.assertTrue(response.data['user']['is_admin'])

    def test_a_suspended_tasker_is_still_refused(self):
        # The rule that already existed must survive the new model: being a
        # tasker with no working profile is a 403, not a silent downgrade
        # to tool access.
        make_account('zed', roles.TASKER, code='W-09', status=WorkerProfile.SUSPENDED)

        response = self._login('zed')

        self.assertEqual(response.status_code, 403)
        self.assertIn('not active for task work', response.data['error'])

    def test_a_tasker_without_a_profile_is_refused_rather_than_downgraded(self):
        user = User.objects.create_user(username='ghost', password=PW)
        Account.objects.create(user=user, role=roles.TASKER)

        response = self._login('ghost')

        self.assertEqual(response.status_code, 403)

    def test_me_reports_the_role_and_capabilities(self):
        user, _, _ = make_account('toolonly', roles.TOOL)
        self.client.force_authenticate(user)

        response = self.client.get('/api/v1/workforce/me/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['role'], roles.TOOL)
        self.assertEqual(response.data['capabilities'], ['studio'])


# ===================================================================== #
# What each role may reach
# ===================================================================== #

class RoleAccessTestCase(APITestCase):

    def setUp(self):
        self.tool_user, _, _ = make_account('toolonly', roles.TOOL)
        self.tasker, _, self.profile = make_account('ana', roles.TASKER, code='W-01')
        self.admin, _, _ = make_account('boss', roles.ADMIN)

    def test_a_tool_user_is_refused_every_workforce_endpoint(self):
        self.client.force_authenticate(self.tool_user)

        for path in ['/api/v1/workforce/tasks/',
                     '/api/v1/workforce/stats/',
                     '/api/v1/workforce/stats/performance/',
                     '/api/v1/workforce/workers/',
                     '/api/v1/workforce/accounts/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403, path)

    def test_a_tool_user_still_gets_their_own_identity(self):
        self.client.force_authenticate(self.tool_user)
        self.assertEqual(self.client.get('/api/v1/workforce/me/').status_code, 200)

    def test_a_tool_user_sees_no_tasks_even_when_the_queue_is_full(self):
        make_task(status=states.NEW)
        make_task(status=states.NEW)
        self.client.force_authenticate(self.tool_user)

        # Not an empty list from an empty queue -- refused outright.
        self.assertEqual(self.client.get('/api/v1/workforce/tasks/').status_code, 403)

    def test_a_tool_user_cannot_claim_a_task(self):
        task = make_task(status=states.NEW)
        self.client.force_authenticate(self.tool_user)

        response = self.client.post(f'/api/v1/workforce/tasks/{task.id}/claim/')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Task.objects.get(pk=task.id).status, states.NEW)

    def test_a_tasker_reaches_the_queue_but_not_administration(self):
        self.client.force_authenticate(self.tasker)

        self.assertEqual(self.client.get('/api/v1/workforce/tasks/').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/workforce/stats/').status_code, 403)
        self.assertEqual(self.client.get('/api/v1/workforce/accounts/').status_code, 403)

    def test_an_administrator_reaches_everything(self):
        self.client.force_authenticate(self.admin)

        for path in ['/api/v1/workforce/tasks/',
                     '/api/v1/workforce/stats/',
                     '/api/v1/workforce/workers/',
                     '/api/v1/workforce/accounts/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200, path)


# ===================================================================== #
# Account management
# ===================================================================== #

class AccountManagementTestCase(APITestCase):

    def setUp(self):
        self.admin, self.admin_account, _ = make_account('boss', roles.ADMIN)
        self.client.force_authenticate(self.admin)
        self.url = '/api/v1/workforce/accounts/'

    def test_creating_a_tool_user_makes_no_worker_profile(self):
        response = self.client.post(self.url, {
            'username': 'newtool', 'password': 'a-long-enough-passphrase',
            'role': roles.TOOL,
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['role'], roles.TOOL)
        self.assertIsNone(response.data['worker_code'])
        self.assertFalse(WorkerProfile.objects.filter(user__username='newtool').exists())

    def test_creating_a_tasker_requires_a_worker_code(self):
        response = self.client.post(self.url, {
            'username': 'newtasker', 'password': 'a-long-enough-passphrase',
            'role': roles.TASKER,
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('worker_code', response.data)
        self.assertFalse(User.objects.filter(username='newtasker').exists())

    def test_creating_a_tasker_makes_the_profile_too(self):
        response = self.client.post(self.url, {
            'username': 'newtasker', 'password': 'a-long-enough-passphrase',
            'role': roles.TASKER, 'worker_code': 'W-42', 'display_name': 'New',
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        profile = WorkerProfile.objects.get(user__username='newtasker')
        self.assertEqual(profile.worker_code, 'W-42')
        self.assertEqual(Account.objects.get(user__username='newtasker').role, roles.TASKER)

    def test_a_created_administrator_gets_the_staff_flag_too(self):
        # So the role and the Django flag agree from the start rather than
        # drifting apart later.
        response = self.client.post(self.url, {
            'username': 'newadmin', 'password': 'a-long-enough-passphrase',
            'role': roles.ADMIN,
        }, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertTrue(User.objects.get(username='newadmin').is_staff)

    def test_a_short_password_is_refused(self):
        response = self.client.post(self.url, {
            'username': 'weak', 'password': 'short', 'role': roles.TOOL,
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('password', response.data)

    def test_a_worker_cannot_manage_accounts(self):
        tasker, _, _ = make_account('ana', roles.TASKER, code='W-01')
        self.client.force_authenticate(tasker)

        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(
            self.client.post(self.url, {
                'username': 'x', 'password': 'a-long-enough-passphrase', 'role': roles.ADMIN,
            }, format='json').status_code,
            403,
        )

    # -- role changes -------------------------------------------------- #

    def test_promoting_a_tool_user_to_tasker_creates_their_profile(self):
        _, account, _ = make_account('toolonly', roles.TOOL)

        response = self.client.post(
            f'{self.url}{account.id}/set-role/',
            {'role': roles.TASKER, 'worker_code': 'W-77'}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['role'], roles.TASKER)
        self.assertEqual(response.data['worker_code'], 'W-77')

    def test_promoting_to_tasker_without_a_code_is_refused(self):
        _, account, _ = make_account('toolonly', roles.TOOL)

        response = self.client.post(
            f'{self.url}{account.id}/set-role/', {'role': roles.TASKER}, format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Account.objects.get(pk=account.id).role, roles.TOOL)

    def test_demoting_a_tasker_who_holds_work_is_refused(self):
        """
        Stranding work is worse than refusing the request. The task would
        stay claimed by someone who can no longer reach it.
        """
        _, account, profile = make_account('ana', roles.TASKER, code='W-01')
        task = make_task(status=states.CLAIMED, claimed_by=profile)

        response = self.client.post(
            f'{self.url}{account.id}/set-role/', {'role': roles.TOOL}, format='json',
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn('still holds 1 task', response.data['error'])
        self.assertEqual(Account.objects.get(pk=account.id).role, roles.TASKER)
        self.assertEqual(Task.objects.get(pk=task.id).claimed_by_id, profile.id)

    def test_demoting_a_tasker_who_holds_nothing_is_allowed(self):
        _, account, _ = make_account('ana', roles.TASKER, code='W-01')

        response = self.client.post(
            f'{self.url}{account.id}/set-role/', {'role': roles.TOOL}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Account.objects.get(pk=account.id).role, roles.TOOL)

    def test_an_administrator_cannot_demote_themselves(self):
        # One accidental self-demotion locks the team out of approvals with
        # no way back except a shell.
        response = self.client.post(
            f'{self.url}{self.admin_account.id}/set-role/',
            {'role': roles.TOOL}, format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('your own administrator role', response.data['error'])
        self.assertEqual(Account.objects.get(pk=self.admin_account.id).role, roles.ADMIN)

    def test_an_invalid_role_is_refused(self):
        _, account, _ = make_account('toolonly', roles.TOOL)

        response = self.client.post(
            f'{self.url}{account.id}/set-role/', {'role': 'superuser'}, format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Account.objects.get(pk=account.id).role, roles.TOOL)

    def test_promoting_to_administrator_sets_the_staff_flag(self):
        _, account, _ = make_account('toolonly', roles.TOOL)

        self.client.post(f'{self.url}{account.id}/set-role/',
                         {'role': roles.ADMIN}, format='json')

        account.refresh_from_db()
        account.user.refresh_from_db()
        self.assertEqual(account.role, roles.ADMIN)
        self.assertTrue(account.user.is_staff)

    def test_demoting_an_administrator_clears_the_staff_flag(self):
        _, account, _ = make_account('other', roles.ADMIN)

        self.client.post(f'{self.url}{account.id}/set-role/',
                         {'role': roles.TOOL}, format='json')

        account.refresh_from_db()
        account.user.refresh_from_db()
        self.assertEqual(account.role, roles.TOOL)
        self.assertFalse(account.user.is_staff)

    def test_a_role_change_to_the_same_role_is_a_no_op(self):
        _, account, _ = make_account('toolonly', roles.TOOL)

        response = self.client.post(f'{self.url}{account.id}/set-role/',
                                    {'role': roles.TOOL}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['role'], roles.TOOL)


# ===================================================================== #
# The existing worker path still sets a role
# ===================================================================== #

class LegacyWorkerCreationTestCase(APITestCase):

    def test_creating_a_worker_through_the_workers_endpoint_sets_the_tasker_role(self):
        admin, _, _ = make_account('boss', roles.ADMIN)
        self.client.force_authenticate(admin)

        response = self.client.post('/api/v1/workforce/workers/', {
            'username': 'fromworkers', 'password': 'a-long-enough-passphrase',
            'worker_code': 'W-55',
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            Account.objects.get(user__username='fromworkers').role, roles.TASKER,
        )

    def test_workers_made_by_the_test_helper_are_taskers(self):
        # `make_worker` is used by 90 existing tests. Its users must land on
        # the right role, or every one of those tests is exercising a
        # different account kind than it thinks.
        user, _ = make_worker('helper', 'W-88')
        self.assertEqual(account_role(user), roles.TASKER)

    def test_admins_made_by_the_test_helper_are_administrators(self):
        user, _ = make_worker('helperboss', 'A-88', is_admin=True)
        self.assertEqual(account_role(user), roles.ADMIN)
        self.assertTrue(is_admin(user))
