"""
Outside Eagle taskers: exact-ID lookup, the 70-minute window, private
copies, and the final draft. See eagle_tasker.py.
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import eagle_tasker, roles, states
from .models import Account, EagleTaskerCopy, Task
from .test_eagle_submit import build_task
from .tests import make_worker


def make_eagle_tasker(username='outside'):
    user = User.objects.create_user(username=username, password='an-adequately-long-passphrase')
    Account.objects.create(user=user, role=roles.EAGLE_TASKER,
                           email_verified_at=timezone.now())
    return user


class TaskerTestCase(TestCase):

    def setUp(self):
        self.user = make_eagle_tasker()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.task = build_task(self_checked=False)
        Task.objects.filter(pk=self.task.pk).update(status=states.NEW, claimed_by=None)
        self.task.refresh_from_db()

    def lookup(self, reference):
        return self.client.post('/api/v1/workforce/tasker/lookup/',
                                {'reference': reference}, format='json')

    def url(self, suffix=''):
        return f'/api/v1/workforce/tasker/tasks/{self.task.id}/{suffix}'


class LookupTestCase(TaskerTestCase):

    def test_finds_a_task_in_the_pool_by_its_exact_id(self):
        response = self.lookup('4333')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], str(self.task.id))
        self.assertEqual(response.data['readiness'], eagle_tasker.READY)
        self.assertIsNone(response.data['left_pool_at'])

    def test_accepts_the_whole_eagle_link(self):
        response = self.lookup('https://transcribe.eaglewebcommerce.com/user/stt/task/4333/show')
        self.assertEqual(response.status_code, 200)

    def test_a_link_from_the_other_product_does_not_match(self):
        response = self.lookup('https://translation.eaglewebcommerce.com/tasks/4333')
        self.assertEqual(response.status_code, 404)

    def test_partial_ids_find_nothing(self):
        self.assertEqual(self.lookup('433').status_code, 404)
        self.assertEqual(self.lookup('').status_code, 404)

    def test_findable_for_seventy_minutes_after_leaving_the_pool_with_a_warning(self):
        left = timezone.now() - timedelta(minutes=60)
        Task.objects.filter(pk=self.task.pk).update(status=states.CANCELLED, cancelled_at=left)
        response = self.lookup('4333')
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data['left_pool_at'])

    def test_not_findable_after_the_window(self):
        left = timezone.now() - timedelta(minutes=71)
        Task.objects.filter(pk=self.task.pk).update(status=states.CANCELLED, cancelled_at=left)
        self.assertEqual(self.lookup('4333').status_code, 404)

    def test_a_tasker_who_already_opened_it_keeps_it_after_the_window(self):
        self.lookup('4333')
        left = timezone.now() - timedelta(minutes=90)
        Task.objects.filter(pk=self.task.pk).update(status=states.CANCELLED, cancelled_at=left)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        # ...but somebody who never opened it does not.
        other = APIClient()
        other.force_authenticate(make_eagle_tasker('second'))
        self.assertEqual(other.get(self.url()).status_code, 404)

    def test_an_untranscribed_task_starts_transcription(self):
        Task.objects.filter(pk=self.task.pk).update(job=None)
        with mock.patch.object(eagle_tasker, 'start_transcription') as start:
            response = self.lookup('4333')
        self.assertEqual(response.data['readiness'], eagle_tasker.NOT_STARTED)
        start.assert_called_once()

    def test_the_summary_says_nothing_about_who_on_our_side_worked_it(self):
        data = self.lookup('4333').data
        for private in ('claimed_by', 'assigned_to', 'deadline_at', 'minutes_required',
                        'polished_by', 'accuracy_check_by'):
            self.assertNotIn(private, data)


class RoleIsolationTestCase(TaskerTestCase):

    def test_an_eagle_tasker_cannot_reach_the_worker_surface(self):
        self.assertEqual(self.client.get('/api/v1/workforce/tasks/').status_code, 403)
        self.assertIn(self.client.get(f'/api/v1/workforce/tasks/{self.task.id}/media/').status_code,
                      (403, 404))
        self.assertIn(self.client.get(f'/api/v1/transcriptions/{self.task.job_id}/').status_code,
                      (403, 404))

    def test_workers_and_tool_users_cannot_reach_the_tasker_surface(self):
        worker, _ = make_worker('worker', 'W-001')
        client = APIClient()
        client.force_authenticate(worker)
        self.assertEqual(client.post('/api/v1/workforce/tasker/lookup/',
                                     {'reference': '4333'}, format='json').status_code, 403)

    def test_login_reports_the_eagle_capability(self):
        response = APIClient().post('/api/v1/workforce/auth/desk-login/', {
            'username': 'outside', 'password': 'an-adequately-long-passphrase'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['capabilities'], ['desk'])
        self.assertNotIn('role', response.data['user'])


class PrivateCopyTestCase(TaskerTestCase):

    def setUp(self):
        super().setUp()
        self.lookup('4333')
        self.rows = self.client.get(self.url('transcript/')).data['segments']

    def test_edits_land_on_the_copy_and_never_on_the_shared_transcript(self):
        first = self.rows[0]
        shared_before = self.task.job.segments.get(pk=first['id']).text

        response = self.client.patch(self.url(f"segments/{first['id']}/"),
                                     {'text': 'Edited by the tasker.'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['is_edited'])
        self.assertEqual(self.task.job.segments.get(pk=first['id']).text, shared_before)

    def test_two_taskers_do_not_see_each_others_edits(self):
        first = self.rows[0]
        self.client.patch(self.url(f"segments/{first['id']}/"), {'text': 'Mine.'}, format='json')

        other = APIClient()
        other.force_authenticate(make_eagle_tasker('second'))
        other.post('/api/v1/workforce/tasker/lookup/', {'reference': '4333'}, format='json')
        theirs = other.get(self.url('transcript/')).data['segments'][0]
        self.assertNotEqual(theirs['text'], 'Mine.')

    def test_timing_cannot_be_edited(self):
        response = self.client.patch(self.url(f"segments/{self.rows[0]['id']}/"),
                                     {'start_time': 1}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_revert_and_split(self):
        row = self.rows[0]
        self.client.patch(self.url(f"segments/{row['id']}/"), {'text': 'Changed.'}, format='json')
        reverted = self.client.post(self.url(f"segments/{row['id']}/revert/")).data
        self.assertFalse(reverted['is_edited'])

        split = self.client.post(self.url(f"segments/{row['id']}/split/"),
                                 {'split_at': 10, 'second_speaker': 'Female_1'}, format='json')
        self.assertEqual(split.status_code, 201)
        self.assertEqual(len(self.client.get(self.url('transcript/')).data['segments']),
                         len(self.rows) + 1)

    def test_speaker_rename_stays_on_the_copy(self):
        self.client.post(self.url('speaker-aliases/'), {'aliases': {'Male_1': 'Female_1'}},
                         format='json')
        self.task.job.refresh_from_db()
        self.assertEqual(self.task.job.speaker_aliases, {})
        draft = self.client.get(self.url('final-draft/')).data['content']
        self.assertIn('Female_1', draft)


class FinalDraftTestCase(TaskerTestCase):

    def setUp(self):
        super().setUp()
        self.lookup('4333')

    def test_the_name_line_is_a_placeholder_and_the_text_is_the_copy(self):
        row = self.client.get(self.url('transcript/')).data['segments'][0]
        self.client.patch(self.url(f"segments/{row['id']}/"),
                          {'text': 'A sentence only this tasker wrote.'}, format='json')

        data = self.client.get(self.url('final-draft/')).data

        self.assertIn(eagle_tasker.NAME_PLACEHOLDER, data['content'])
        self.assertIn('A sentence only this tasker wrote.', data['content'])
        self.assertIn('-->', data['content'])
        self.assertFalse(any(eagle_tasker.NAME_PLACEHOLDER in f['message']
                             for f in data['findings']))

    def test_saving_the_accuracy_check_clears_that_finding(self):
        codes = lambda: {f['code'] for f in self.client.get(self.url('final-draft/')).data['findings']}
        self.assertIn('self-check-not-completed', codes())
        self.client.post(self.url('check/'), {'notes': 'Nothing found.'}, format='json')
        self.assertNotIn('self-check-not-completed', codes())

    def test_the_shared_task_is_untouched_by_the_tasker_check(self):
        self.client.post(self.url('check/'), {'notes': 'Nothing found.'}, format='json')
        self.task.refresh_from_db()
        self.assertIsNone(self.task.accuracy_check_at)


class TranslationTestCase(TestCase):

    def setUp(self):
        self.user = make_eagle_tasker()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.task = build_task(kind=Task.TRANSLATION, self_checked=False)
        Task.objects.filter(pk=self.task.pk).update(status=states.NEW, claimed_by=None)
        self.client.post('/api/v1/workforce/tasker/lookup/', {
            'reference': 'https://translation.eaglewebcommerce.com/tasks/4333'}, format='json')
        self.base = f'/api/v1/workforce/tasker/tasks/{self.task.id}/'

    def test_the_polished_french_is_the_copys_and_reaches_the_final_draft(self):
        response = self.client.post(self.base + 'translation/',
                                    {'text': 'Un texte poli par le traducteur.'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.polished_text, '')

        content = self.client.get(self.base + 'final-draft/').data['content']
        self.assertIn('Un texte poli par le traducteur.', content)
        self.assertIn(eagle_tasker.NAME_PLACEHOLDER, content)

    def test_retranslate_uses_the_taskers_english_and_is_counted(self):
        copy = EagleTaskerCopy.objects.get(task=self.task, owner=self.user)
        row = copy.segments[0]
        self.client.patch(self.base + f"segments/{row['id']}/",
                          {'text': 'The corrected English line.'}, format='json')

        with mock.patch.object(eagle_tasker, 'retranslate_line',
                               wraps=eagle_tasker.retranslate_line), \
             mock.patch('core.services.ai_assist.is_configured', return_value=True), \
             mock.patch('core.services.ai_assist.processor.AIPostProcessor') as processor:
            processed = mock.Mock(processed_text='La ligne corrigée.')
            batch = mock.Mock(segments=[processed], cache_misses=1, model_name='m')
            batch.usage.prompt_tokens = batch.usage.completion_tokens = batch.usage.total_tokens = 1
            processor.return_value.iter_batches.return_value = [batch]
            response = self.client.post(self.base + 'retranslate/', {'index': 0}, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['segments'][0]['effective_text'], 'La ligne corrigée.')
        sent = processor.return_value.iter_batches.call_args[0][0]
        self.assertEqual(sent[0].text, 'The corrected English line.')
        copy.refresh_from_db()
        self.assertEqual(copy.retranslate_count, 1)


class PerimeterTestCase(TestCase):
    """
    Every API route in the project, called as an Eagle tasker.

    Walks the real URL configuration rather than a hand-written list, so an
    endpoint added later is covered without anyone remembering to add it.
    """

    def setUp(self):
        from rest_framework.authtoken.models import Token
        self.user = make_eagle_tasker()
        self.token = Token.objects.create(user=self.user)

    @staticmethod
    def every_api_path():
        import re
        import uuid
        from django.urls import get_resolver

        def walk(patterns, prefix=''):
            for p in patterns:
                route = prefix + str(p.pattern)
                if hasattr(p, 'url_patterns'):
                    yield from walk(p.url_patterns, route)
                else:
                    yield route

        sample = str(uuid.uuid4())
        for route in walk(get_resolver().url_patterns):
            route = route.lstrip('^').rstrip('$').replace(chr(92), '')
            route = re.sub(r'<uuid:\w+>', sample, route)
            route = re.sub(r'<int:\w+>', '1', route)
            route = re.sub(r'<(str:)?\w+>', 'x', route)
            route = re.sub(r'\(\?P<\w+>[^)]*\)', 'x', route)
            if route.startswith('api/') and '(' not in route and '[' not in route:
                yield '/' + route

    def test_everything_outside_the_allow_list_is_not_found(self):
        from .perimeter import ALLOWED

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        checked = 0
        for path in self.every_api_path():
            if any(p.match(path) for p in ALLOWED):
                continue
            for method in ('get', 'post', 'patch', 'delete'):
                response = getattr(client, method)(path, {}, format='json')
                self.assertEqual(response.status_code, 404, f'{method.upper()} {path}')
            checked += 1
        # Sanity: the walk really found the studio, worker and admin routes.
        self.assertGreater(checked, 25)

    def test_the_studio_cannot_be_used_with_a_session_either(self):
        client = APIClient()
        client.force_login(self.user)
        self.assertEqual(client.post('/api/v1/transcriptions/', {}, format='json').status_code, 404)
        self.assertEqual(client.get('/api/v1/projects/').status_code, 404)

    def test_the_allow_list_still_works(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        self.assertEqual(client.get('/api/v1/workforce/me/').status_code, 200)
        self.assertEqual(client.post('/api/v1/workforce/tasker/lookup/',
                                     {'reference': '1'}, format='json').status_code, 404)
        self.assertEqual(client.get('/api/v1/workforce/tasker/activity/').status_code, 404)

    def test_workers_are_not_fenced(self):
        worker, _ = make_worker('worker', 'W-001')
        client = APIClient()
        client.force_authenticate(worker)
        self.assertEqual(client.get('/api/v1/workforce/tasks/').status_code, 200)
        self.assertEqual(client.get('/api/v1/languages/').status_code, 200)

    def test_a_disabled_tasker_is_signed_out_everywhere(self):
        from rest_framework.authtoken.models import Token
        admin = User.objects.create_user('boss', password='an-adequately-long-passphrase',
                                         is_staff=True)
        client = APIClient()
        client.force_authenticate(admin)
        account = Account.objects.get(user=self.user)
        response = client.post(f'/api/v1/workforce/accounts/{account.id}/set-active/',
                               {'active': False}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Token.objects.filter(user=self.user).exists())
        login = APIClient().post('/api/v1/workforce/auth/desk-login/', {
            'username': 'outside', 'password': 'an-adequately-long-passphrase'}, format='json')
        self.assertEqual(login.status_code, 401)


class CopyLimitsTestCase(TaskerTestCase):

    def test_an_oversized_line_is_refused(self):
        self.lookup('4333')
        row = self.client.get(self.url('transcript/')).data['segments'][0]
        response = self.client.patch(self.url(f"segments/{row['id']}/"),
                                     {'text': 'x' * 20001}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_document_metadata_carries_only_the_speaker_doubts(self):
        self.task.job.document_metadata = {'owner': 'someone', 'uncertain_speakers': []}
        self.task.job.save()
        self.lookup('4333')
        meta = self.client.get(self.url('transcript/')).data['document_metadata']
        self.assertEqual(set(meta), {'uncertain_speakers'})
