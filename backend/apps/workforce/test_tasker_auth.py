"""
Public sign-up, Google sign-in and the login lockout -- the parts of the
system a stranger on the internet can reach without an account.
"""
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from . import roles, tasker_auth
from .models import Account, ExternalIdentity

SIGNUP = '/api/v1/workforce/auth/signup/'
GOOGLE = '/api/v1/workforce/auth/google/'
LOGIN = '/api/v1/workforce/auth/login/'
GOOD = {'username': 'new.tasker', 'email': 'New@Example.com',
        'password': 'Kibo_2026'}


class SignupTestCase(TestCase):

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_creates_a_tasker_and_signs_them_in(self):
        response = self.client.post(SIGNUP, GOOD, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data['token'])
        self.assertEqual(response.data['capabilities'], ['desk'])
        self.assertNotIn('role', response.data['user'])
        user = User.objects.get(username='new.tasker')
        self.assertEqual(user.email, 'new@example.com')
        self.assertEqual(Account.objects.get(user=user).role, roles.EAGLE_TASKER)
        self.assertFalse(user.is_staff)

    def test_the_request_cannot_choose_a_role(self):
        self.client.post(SIGNUP, {**GOOD, 'role': 'admin', 'is_staff': True,
                                  'is_superuser': True}, format='json')
        user = User.objects.get(username='new.tasker')
        self.assertEqual(Account.objects.get(user=user).role, roles.EAGLE_TASKER)
        self.assertFalse(user.is_staff or user.is_superuser)

    def test_usernames_are_a_safe_alphabet(self):
        bad_names = [
            "robert'); DROP TABLE auth_user;--", '<script>x</script>', 'ab',
            'admin', 'Admin', 'name with space',
            'аdmin',  # Cyrillic "a": a look-alike of "admin"
            'x' * 31, '.dot',
        ]
        for bad in bad_names:
            response = self.client.post(SIGNUP, {**GOOD, 'username': bad}, format='json')
            self.assertEqual(response.status_code, 400, bad)
            self.assertIn('username', response.data['fields'])
        self.assertEqual(User.objects.count(), 0)

    def test_duplicate_username_and_email_are_refused_case_insensitively(self):
        self.client.post(SIGNUP, GOOD, format='json')
        response = self.client.post(SIGNUP, {**GOOD, 'username': 'NEW.TASKER',
                                             'email': 'other@example.com'}, format='json')
        self.assertIn('username', response.data['fields'])
        response = self.client.post(SIGNUP, {**GOOD, 'username': 'someone',
                                             'email': 'NEW@example.com'}, format='json')
        self.assertIn('email', response.data['fields'])

    def test_weak_passwords_are_refused(self):
        for weak in ('Kibo_20',          # 7 characters
                     'kibo_2026',        # no uppercase
                     'KIBO_2026',        # no lowercase
                     'Kibo_twenty',      # no number
                     'Kibo2026x',        # no symbol
                     'Password1!',       # a common password
                     'New.tasker1'):     # too like the username
            response = self.client.post(SIGNUP, {**GOOD, 'password': weak}, format='json')
            self.assertEqual(response.status_code, 400, weak)
            self.assertIn('password', response.data['fields'], weak)

    def test_eight_characters_with_every_kind_is_enough(self):
        self.assertEqual(self.client.post(SIGNUP, GOOD, format='json').status_code, 201)

    def test_the_honeypot_turns_bots_away(self):
        response = self.client.post(SIGNUP, {**GOOD, 'website': 'http://spam'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.exists())

    @override_settings(TASKER_SIGNUP_OPEN='0')
    def test_the_kill_switch_closes_sign_up(self):
        self.assertEqual(self.client.post(SIGNUP, GOOD, format='json').status_code, 403)


class SignupThrottleTestCase(TestCase):
    """Throttling is off in the test settings, so the rate is set here directly."""

    def test_one_address_cannot_mass_create_accounts(self):
        cache.clear()
        client = APIClient()
        rates = {'signup': '2/hour', 'signup_day': '10/day'}
        with mock.patch.object(tasker_auth.SignupBurstThrottle, 'THROTTLE_RATES', rates), \
             mock.patch.object(tasker_auth.SignupDailyThrottle, 'THROTTLE_RATES', rates):
            codes = [client.post(SIGNUP, {**GOOD, 'username': f'user{i}',
                                          'email': f'u{i}@example.com'}, format='json').status_code
                     for i in range(3)]
        self.assertEqual(codes, [201, 201, 429])


class LoginLockoutTestCase(TestCase):

    def test_ten_wrong_passwords_lock_the_account_whoever_asks_next(self):
        cache.clear()
        User.objects.create_user('target', password='the-right-passphrase-99')
        client = APIClient()
        for _ in range(10):
            client.post(LOGIN, {'username': 'target', 'password': 'wrong'}, format='json')
        right = client.post(LOGIN, {'username': 'target',
                                    'password': 'the-right-passphrase-99'}, format='json')
        self.assertEqual(right.status_code, 429)


@override_settings(GOOGLE_OAUTH_CLIENT_ID='client-123.apps.googleusercontent.com')
class GoogleSignInTestCase(TestCase):

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def google(self, claims):
        with mock.patch.object(tasker_auth, 'verify_google_credential', return_value=claims):
            return self.client.post(GOOGLE, {'credential': 'signed.jwt.token'}, format='json')

    def test_a_new_google_user_becomes_a_tasker(self):
        response = self.google({'sub': 'g-1', 'email': 'Ann.Lee@gmail.com', 'email_verified': True})
        self.assertEqual(response.status_code, 201)
        link = ExternalIdentity.objects.get(subject='g-1')
        self.assertEqual(Account.objects.get(user=link.user).role, roles.EAGLE_TASKER)
        self.assertFalse(link.user.has_usable_password())
        # The second time is a sign-in, not a second account.
        self.assertEqual(self.google({'sub': 'g-1', 'email': 'ann.lee@gmail.com',
                                      'email_verified': True}).status_code, 200)
        self.assertEqual(User.objects.count(), 1)

    def test_an_unverified_google_email_is_refused(self):
        self.assertEqual(self.google({'sub': 'g-2', 'email': 'x@gmail.com',
                                      'email_verified': False}).status_code, 401)

    def test_an_existing_email_is_never_linked_automatically(self):
        User.objects.create_user('squatter', email='victim@gmail.com', password='whatever-long-1')
        response = self.google({'sub': 'g-3', 'email': 'victim@gmail.com', 'email_verified': True})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_a_staff_account_cannot_be_reached_through_google(self):
        staff = User.objects.create_user('boss', is_staff=True)
        Account.objects.create(user=staff, role=roles.ADMIN)
        ExternalIdentity.objects.create(user=staff, provider='google', subject='g-4')
        self.assertEqual(self.google({'sub': 'g-4', 'email': 'b@gmail.com',
                                      'email_verified': True}).status_code, 403)

    def test_a_forged_token_is_rejected(self):
        response = self.client.post(GOOGLE, {'credential': 'not-a-real-token'}, format='json')
        self.assertEqual(response.status_code, 401)

    @override_settings(GOOGLE_OAUTH_CLIENT_ID='')
    def test_without_a_client_id_google_is_off(self):
        self.assertEqual(self.client.post(GOOGLE, {'credential': 'x'}, format='json').status_code, 404)


class AnonymousPerimeterTestCase(TestCase):
    """What a stranger with no account can learn by probing."""

    PUBLIC = ('/api/v1/workforce/auth/',)

    def test_no_route_answers_a_stranger_with_data(self):
        from .test_eagle_tasker import PerimeterTestCase

        client = APIClient()
        checked = 0
        for path in PerimeterTestCase.every_api_path():
            if path.startswith(self.PUBLIC):
                continue
            response = client.get(path)
            self.assertIn(response.status_code, (401, 403, 404, 405), path)
            checked += 1
        self.assertGreater(checked, 25)

    def test_there_is_no_api_index_to_browse(self):
        client = APIClient()
        self.assertEqual(client.get('/api/v1/').status_code, 404)
        self.assertEqual(client.get('/api/v1/workforce/').status_code, 404)


class EmailCodeTestCase(TestCase):
    """A password sign-up is not usable until the emailed code is entered."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        response = self.client.post(SIGNUP, GOOD, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {response.data['token']}")
        self.signup = response

    def code(self):
        import re
        return re.search(r'\b(\d{6})\b', mail.outbox[-1].subject).group(1)

    def test_sign_up_emails_a_code_and_starts_unconfirmed(self):
        self.assertFalse(self.signup.data['email_verified'])
        self.assertEqual(mail.outbox[-1].to, ['new@example.com'])
        self.assertFalse(self.client.get('/api/v1/workforce/me/').data['email_verified'])

    def test_an_unconfirmed_account_cannot_open_tasks(self):
        response = self.client.post('/api/v1/workforce/tasker/lookup/',
                                    {'reference': '1'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_the_right_code_confirms_the_account(self):
        response = self.client.post('/api/v1/workforce/auth/verify-email/',
                                    {'code': self.code()}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.get('/api/v1/workforce/me/').data['email_verified'])
        self.assertEqual(self.client.post('/api/v1/workforce/tasker/lookup/',
                                          {'reference': '1'}, format='json').status_code, 404)

    def test_five_wrong_codes_burn_the_code(self):
        right = self.code()
        wrong = '000000' if right != '000000' else '111111'
        for _ in range(5):
            self.client.post('/api/v1/workforce/auth/verify-email/', {'code': wrong}, format='json')
        response = self.client.post('/api/v1/workforce/auth/verify-email/',
                                    {'code': right}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_only_a_hash_is_stored(self):
        from .models import EmailCode
        stored = EmailCode.objects.get(user__username='new.tasker').code_hash
        self.assertNotIn(self.code(), stored)

    def test_a_new_code_replaces_the_old_one(self):
        old = self.code()
        self.client.post('/api/v1/workforce/auth/resend-code/')
        new = self.code()
        if old != new:
            self.assertEqual(self.client.post('/api/v1/workforce/auth/verify-email/',
                                              {'code': old}, format='json').status_code, 400)

    @override_settings(GOOGLE_OAUTH_CLIENT_ID='client-123.apps.googleusercontent.com')
    def test_google_accounts_are_already_confirmed(self):
        with mock.patch.object(tasker_auth, 'verify_google_credential',
                               return_value={'sub': 'g-9', 'email': 'g@gmail.com',
                                             'email_verified': True}):
            response = APIClient().post(GOOGLE, {'credential': 'x'}, format='json')
        self.assertTrue(response.data['email_verified'])


class SeparateDoorsTestCase(TestCase):
    """Each sign-in page admits only its own kind of account."""

    def setUp(self):
        cache.clear()
        from .tests import make_worker
        self.worker, _ = make_worker('worker', 'W-001')
        self.admin = User.objects.create_user('boss', password='an-adequately-long-passphrase',
                                              is_staff=True)
        self.tasker = User.objects.create_user('outside', password='an-adequately-long-passphrase')
        Account.objects.create(user=self.tasker, role=roles.EAGLE_TASKER)

    def attempt(self, path, username):
        return APIClient().post(path, {'username': username,
                                       'password': 'an-adequately-long-passphrase'}, format='json')

    def test_workers_and_admins_cannot_sign_in_at_the_tasker_door(self):
        for name in ('worker', 'boss'):
            response = self.attempt('/api/v1/workforce/auth/desk-login/', name)
            self.assertEqual(response.status_code, 401, name)
            self.assertEqual(response.data['error'], 'Invalid credentials.')
            self.assertNotIn('token', response.data)

    def test_taskers_cannot_sign_in_at_the_workers_door(self):
        response = self.attempt(LOGIN, 'outside')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data['error'], 'Invalid credentials.')

    def test_each_door_still_admits_its_own(self):
        self.assertEqual(self.attempt('/api/v1/workforce/auth/desk-login/', 'outside').status_code, 200)
        self.assertEqual(self.attempt(LOGIN, 'worker').status_code, 200)
        self.assertEqual(self.attempt(LOGIN, 'boss').status_code, 200)

    def test_an_admin_session_cannot_reach_the_tasker_area(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.post('/api/v1/workforce/tasker/lookup/', {'reference': '1'}, format='json')
        self.assertEqual(response.status_code, 403)
