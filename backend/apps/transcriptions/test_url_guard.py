"""
Refusing to fetch a URL that points back inside the network.

WHY THIS IS TESTED AT THE ENDPOINTS AND NOT ONLY THE FUNCTION. The check
this replaces was correct, well tested, and called from nowhere but its own
tests -- so the platform fetched whatever it was given for as long as it has
existed. Asserting the helper works proves nothing about whether anything
uses it. Every test here goes through a real request.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from core.services.url_guard import UnsafeURL, check_url

#: The address that matters on a cloud box: most providers serve instance
#: credentials from it to anything that asks, and SSRF is how it is reached.
METADATA = 'http://169.254.169.254/latest/meta-data/iam/security-credentials/'


class UrlGuardTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('ana', password='pw-2026-abcdef')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    # ------------------------------------------------------------------ #
    # The function
    # ------------------------------------------------------------------ #

    def test_it_refuses_the_addresses_that_matter(self):
        for url in (METADATA,
                    'http://localhost:8002/api/v1/workforce/accounts/',
                    'http://127.0.0.1/x.mp3',
                    'http://10.0.0.5/x.mp3',
                    'http://192.168.1.1/',
                    'http://[::1]/x.mp3',
                    'http://0.0.0.0/'):
            with self.subTest(url=url):
                with self.assertRaises(UnsafeURL):
                    check_url(url)

    def test_it_refuses_schemes_that_are_not_the_web(self):
        # file:// reads the disk; the rest exist mostly as SSRF vectors.
        for url in ('file:///etc/passwd', 'ftp://example.com/x',
                    'gopher://example.com/', 'data:text/plain,hello'):
            with self.subTest(url=url):
                with self.assertRaises(UnsafeURL):
                    check_url(url)

    def test_it_allows_the_addresses_the_platform_actually_uses(self):
        for url in ('https://translation.eaglewebcommerce.com/tasks/3940',
                    'https://transcribe.eaglewebcommerce.com/user/stt/awards',
                    'https://www.youtube.com/watch?v=abc',
                    'https://example.com/episode.mp3'):
            with self.subTest(url=url):
                self.assertEqual(check_url(url), url)

    def test_an_internal_host_may_be_allowed_deliberately(self):
        """For a deployment whose media lives on an intranet. Off unless
        asked for, so the safe posture is the one you get by default."""
        self.assertEqual(check_url('http://10.0.0.5/x.mp3', allow_private=True),
                         'http://10.0.0.5/x.mp3')

    def test_an_unresolvable_name_is_not_treated_as_an_attack(self):
        # Let the fetch fail with a real network error rather than accusing
        # somebody of something because their DNS is down.
        url = 'https://no-such-host-exists-here-2026.invalid/a.mp3'
        self.assertEqual(check_url(url), url)

    # ------------------------------------------------------------------ #
    # The doors it has to actually be standing in
    # ------------------------------------------------------------------ #

    def test_discovery_refuses_a_metadata_address(self):
        response = self.client.post('/api/v1/discover-series/',
                                    {'source_url': METADATA}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('private network', str(response.data).lower())

    def test_a_job_cannot_be_created_pointing_at_the_metadata_service(self):
        from apps.transcriptions.models import Project

        project = Project.objects.create(name='guarded')
        response = self.client.post('/api/v1/transcriptions/', {
            'project': str(project.id),
            'input_source': 'url',
            'source_url': METADATA,
        }, format='json')

        self.assertEqual(response.status_code, 400)

    def test_a_real_address_still_gets_through_the_door(self):
        from apps.transcriptions.models import Project

        project = Project.objects.create(name='allowed')
        response = self.client.post('/api/v1/transcriptions/', {
            'project': str(project.id),
            'input_source': 'url',
            'source_url': 'https://example.com/episode.mp3',
        }, format='json')

        self.assertIn(response.status_code, (200, 201))
