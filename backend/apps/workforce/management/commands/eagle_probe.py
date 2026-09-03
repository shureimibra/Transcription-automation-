"""
Can this machine reach Eagle's login pages without being challenged?

WHY THIS RUNS BEFORE AUTO-LOGIN IS BUILT, NOT AFTER. Cloudflare is adaptive
and a datacenter IP is far more likely to be challenged than a residential
or mobile one. Both login pages answered cleanly from a home connection;
that says nothing about a VPS. If the box IS challenged, automatic login is
dead on arrival and the design has to change -- so the answer is worth one
request BEFORE a day is spent building on the assumption.

WHAT IT ACTUALLY ANSWERS, in one line per product:

  status                 did the page load at all
  cloudflare_challenge   was it the real page or "Just a moment..."
  csrf_token_present     is the token auto-login would need actually there

SPENDS NOTHING AND NEEDS NOTHING. No credentials, no cookie, no minutes, no
provider quota. It fetches two public login pages, which is what a browser
does before anybody types anything. Safe to run on a box that has not been
configured yet -- which is the point, because that is exactly when you need
the answer.

    manage.py eagle_probe

The IP it reports is the one Cloudflare sees. If that is a datacenter range
and the result is clean, the clearance treadmill is over: cf_clearance is
bound to an IP, and a static IP stops it rotating.
"""
import re
import socket
import urllib.error
import urllib.request

from django.core.management.base import BaseCommand

from core.services.eagle_client import TRANSCRIBE_HOST, TRANSLATE_HOST, USER_AGENT

#: Where each product asks for a password.
LOGIN_PAGES = (
    ('transcription', f'{TRANSCRIBE_HOST}/login'),
    ('translation', f'{TRANSLATE_HOST}/login'),
)

#: What an interstitial looks like. Cloudflare answers 200 with a holding
#: page, so the status code alone reports success on a challenge.
#:
#: 'challenge-platform' IS DELIBERATELY ABSENT, having been here first and
#: been wrong. Cloudflare injects /cdn-cgi/challenge-platform/ script tags
#: into ORDINARY pages as part of bot management, so matching it reported a
#: challenge on both products from a residential connection that was not
#: being challenged at all -- the exact false verdict this command exists to
#: avoid, and it would have said "do not build auto-login" on a machine
#: where auto-login is perfectly viable.
_CHALLENGE_MARKERS = (
    'just a moment', 'cf-browser-verification', 'cf_chl_opt',
    'checking your browser', 'enable javascript and cookies to continue',
)

#: Laravel's CSRF token, in either place it appears. The meta tag is what a
#: single-page front end reads before sending X-XSRF-TOKEN; the hidden input
#: is what a server-rendered form posts.
_CSRF_META = 'name="csrf-token"'
_CSRF_INPUT = 'name="_token"'

TIMEOUT = 30


class Command(BaseCommand):
    help = ('Check whether this machine can reach Eagle login pages without '
            'a Cloudflare challenge. Spends nothing; needs no credentials.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--show-body', action='store_true',
            help='Print the first 400 characters of each response. Use when '
                 'the verdict is surprising and you want to see why.')

    def handle(self, *args, **options):
        self.stdout.write(f'user-agent : {USER_AGENT[:72]}...')
        self.stdout.write(f'public IP  : {self._public_ip()}')
        self.stdout.write('')

        verdicts = []
        for product, url in LOGIN_PAGES:
            verdict = self._probe(product, url, options['show_body'])
            verdicts.append(verdict)

        self.stdout.write('')
        self._conclude(verdicts)

    # ------------------------------------------------------------------ #

    def _probe(self, product, url, show_body):
        request = urllib.request.Request(url, headers={
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                status = response.status
                body = response.read(200_000).decode('utf-8', 'replace')
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = exc.read(200_000).decode('utf-8', 'replace')
        except Exception as exc:
            self.stdout.write(self.style.ERROR(
                f'{product:<14} could not connect: {type(exc).__name__}'))
            return {'product': product, 'ok': False, 'reason': 'unreachable'}

        lowered = body.lower()
        challenged = any(marker in lowered for marker in _CHALLENGE_MARKERS)
        has_csrf = (_CSRF_META in lowered) or (_CSRF_INPUT in lowered)

        # HOW THE LOGIN IS SHAPED, because it decides how auto-login is
        # written and the two products do not agree. Transcription serves a
        # real Laravel form with a _token input; translation serves a
        # single-page shell whose login is an API call carrying
        # X-XSRF-TOKEN. One implementation will not cover both.
        has_form = '<form' in lowered and 'type="password"' in lowered
        shape = 'form-post' if has_form else (
            'api-post' if _CSRF_META in lowered else 'unknown')

        # A clean result is all three: the page loaded, it is the real page,
        # and the token a login would need is on it.
        ok = status == 200 and not challenged and has_csrf
        line = (f'{product:<14} status={status}  '
                f'cloudflare_challenge={challenged}  '
                f'csrf_token_present={has_csrf}  '
                f'login={shape}')
        self.stdout.write(self.style.SUCCESS(line) if ok
                          else self.style.WARNING(line))

        if show_body:
            self.stdout.write('    ' + re.sub(r'\s+', ' ', body[:400]))

        return {'product': product, 'ok': ok, 'status': status,
                'challenged': challenged, 'has_csrf': has_csrf,
                'shape': shape}

    def _conclude(self, verdicts):
        """Say what this means for auto-login, rather than leaving the
        reader to work it out from three booleans per row."""
        if all(v.get('ok') for v in verdicts):
            self.stdout.write(self.style.SUCCESS(
                'BOTH PAGES ARE CLEAN FROM THIS MACHINE.'))
            self.stdout.write(
                'Automatic login is viable here: fetch the page for the token, '
                'POST the credentials, keep the session cookie.')
            shapes = ', '.join(f"{v['product']}={v['shape']}" for v in verdicts)
            self.stdout.write(f'Login shapes: {shapes}.')
            if len({v['shape'] for v in verdicts}) > 1:
                self.stdout.write(
                    'The two products do not log in the same way, so this '
                    'needs two implementations: a form POST carrying _token '
                    'for one, an API POST carrying X-XSRF-TOKEN for the other.')
            self.stdout.write(
                'Remember it fixes the Laravel session (~2h idle), NOT '
                'cf_clearance, which is IP-bound. A static IP is what ends '
                'that half.')
            return

        challenged = [v['product'] for v in verdicts if v.get('challenged')]
        if challenged:
            self.stdout.write(self.style.ERROR(
                f'CHALLENGED ON: {", ".join(challenged)}.'))
            self.stdout.write(
                'Do NOT build automatic login against this machine — a login '
                'POST would be answered with an interstitial, not a session. '
                'This is the datacenter-IP case the design was warned about. '
                'Options: a residential/mobile egress, a browser-based login '
                'that can solve the challenge, or staying on pasted cookies.')
            return

        missing = [v['product'] for v in verdicts
                   if v.get('ok') is False and not v.get('challenged')]
        self.stdout.write(self.style.WARNING(
            f'NOT CLEAN ON: {", ".join(missing)}.'))
        self.stdout.write(
            'The page loaded but did not look like a login form. Re-run with '
            '--show-body: the markup may simply have changed, in which case '
            'the token markers in this command need updating.')

    @staticmethod
    def _public_ip():
        """
        The address Cloudflare sees, which is the one cf_clearance binds to.

        Best-effort: a box with no outbound DNS still runs the probe, and a
        missing IP is a cosmetic loss rather than a reason to fail.
        """
        try:
            request = urllib.request.Request(
                'https://api.ipify.org', headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.read(64).decode('utf-8', 'replace').strip()
        except Exception:
            try:
                return socket.gethostbyname(socket.gethostname())
            except Exception:
                return 'unknown'
