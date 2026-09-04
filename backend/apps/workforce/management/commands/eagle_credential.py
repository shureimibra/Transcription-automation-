"""
Store or check the Eagle session, without it passing through a browser.

WHY A COMMAND AND NOT A FORM. The session is the platform's credential, not a
worker's, and the fewer places it can travel the better. Typed at a terminal
by the operator it never crosses the frontend, never sits in a request log,
and never appears in a serializer. It is also read from stdin rather than an
argument, because an argument lands in shell history and in the process list.

    manage.py eagle_credential --show
    manage.py eagle_credential --set transcription
    manage.py eagle_credential --check translation
    manage.py eagle_credential --set-login transcription
    manage.py eagle_credential --clear-login transcription

TWO INDEPENDENT CREDENTIALS, ONE ROW. `--set` stores a cookie pasted out of a
real browser session -- always supported, always the fallback. `--set-login`
stores a username/password that the platform can use to obtain a cookie on
its own when the pasted one eventually lapses (see EagleService._guard's
retry-once-on-expiry). Neither requires the other; a row with only a cookie
behaves exactly as it always has.
"""
import getpass
import sys

from django.core.management.base import BaseCommand, CommandError

from apps.workforce.credentials import (
    PRODUCT_CHOICES, TRANSCRIPTION, TRANSLATION, EagleCredential,
)


class Command(BaseCommand):
    help = "Store, inspect or verify the platform's Eagle session cookies and login."

    def add_arguments(self, parser):
        parser.add_argument('--set', metavar='PRODUCT',
                            choices=[TRANSCRIPTION, TRANSLATION],
                            help='Read a cookie header from stdin and store it.')
        parser.add_argument('--check', metavar='PRODUCT',
                            choices=[TRANSCRIPTION, TRANSLATION],
                            help='Ask the platform whether the session still works.')
        parser.add_argument('--show', action='store_true',
                            help='Status only. Never prints cookie or password material.')
        parser.add_argument('--set-login', metavar='PRODUCT',
                            choices=[TRANSCRIPTION, TRANSLATION],
                            help='Store a username/password for automatic re-login, '
                                 'then verify it with a real login.')
        parser.add_argument('--clear-login', metavar='PRODUCT',
                            choices=[TRANSCRIPTION, TRANSLATION],
                            help='Remove stored login credentials. Leaves the cookie untouched.')

    def handle(self, *args, **options):
        if options['set']:
            return self._store(options['set'])
        if options['set_login']:
            return self._store_login(options['set_login'])
        if options['clear_login']:
            return self._clear_login(options['clear_login'])
        if options['check']:
            return self._check(options['check'])
        return self._show()

    # ------------------------------------------------------------------ #

    def _store(self, product):
        self.stdout.write(
            f'Paste the full Cookie header for {product}, then press Enter.\n'
            'In the browser: DevTools > Network > any request > Request Headers > Cookie.\n'
        )
        cookie = sys.stdin.readline().strip()
        if not cookie:
            raise CommandError('Nothing read; no credential stored.')

        credential, _ = EagleCredential.objects.get_or_create(product=product)
        credential.set_cookie(cookie)
        credential.save()

        self.stdout.write(self.style.SUCCESS(
            f'Stored an encrypted session for {product} ({len(cookie)} characters).'))
        self.stdout.write('Verifying...')
        self._check(product)

    def _store_login(self, product):
        username = input(f'Eagle username/email for {product}: ').strip()
        if not username:
            raise CommandError('Nothing entered; no login credentials stored.')
        # getpass, not stdin.readline() -- the password specifically gets a
        # hidden prompt, same as `worker password`. The username above stays
        # plain input(): it is not a secret, and showing what was typed is
        # worth more here than hiding it.
        password = getpass.getpass(f'Eagle password for {product}: ')
        if not password:
            raise CommandError('Nothing entered; no login credentials stored.')

        credential, _ = EagleCredential.objects.get_or_create(product=product)
        credential.set_login(username, password)
        credential.save()
        # Out of scope from here on, deliberately: neither variable is
        # touched again in this method.
        del password

        self.stdout.write(self.style.SUCCESS(f'Stored login credentials for {product}.'))
        self.stdout.write('Verifying with a real login...')

        from apps.workforce.eagle import (
            EagleAuthError, EagleIntegrationError, EagleLoginFailed, EagleNotConfigured,
        )
        from apps.workforce.eagle_impl import EagleService

        try:
            result = EagleService().login(product)
        except EagleLoginFailed as exc:
            self.stdout.write(self.style.ERROR(f'  {product}: login was rejected.'))
            self.stdout.write(f'    {exc.detail}')
            return
        except (EagleAuthError, EagleIntegrationError, EagleNotConfigured) as exc:
            self.stdout.write(self.style.ERROR(f'  {product}: {exc.safe_message}'))
            self.stdout.write(f'    {exc.detail}')
            return

        self.stdout.write(self.style.SUCCESS(
            f"  {product}: logged in as {result.get('account') or 'unknown'}, "
            f"minutes={result.get('minutes_balance')}"))

    def _clear_login(self, product):
        credential = EagleCredential.objects.filter(product=product).first()
        if credential is None or not credential.has_login:
            self.stdout.write(self.style.WARNING(
                f'No login credentials stored for {product}. Nothing to clear.'))
            return
        credential.clear_login()
        self.stdout.write(self.style.SUCCESS(
            f'Cleared login credentials for {product}. The stored cookie is untouched.'))

    def _check(self, product):
        from apps.workforce.eagle import EagleAuthError, EagleNotConfigured
        from apps.workforce.eagle_impl import EagleService

        try:
            health = EagleService().check_session(product)
        except EagleNotConfigured as exc:
            self.stdout.write(self.style.WARNING(f'  {product}: {exc.safe_message}'))
            return
        except EagleAuthError as exc:
            self.stdout.write(self.style.ERROR(
                f'  {product}: the platform rejected this session.'))
            self.stdout.write(f'    {exc.detail}')
            return

        self.stdout.write(self.style.SUCCESS(
            f"  {product}: active as {health.get('account') or 'unknown'}, "
            f"minutes={health.get('minutes_balance')}"))

        minutes = health.get('minutes_balance')
        if minutes is not None and minutes < 30:
            # The real throughput cap. Better said here than discovered by a
            # worker whose claim is refused mid-shift.
            self.stdout.write(self.style.WARNING(
                f'    Only {minutes} minutes left. A 25-minute task needs ~25; '
                'top up before a shift starts.'))

    def _show(self):
        credentials = EagleCredential.objects.all()
        if not credentials:
            self.stdout.write(self.style.WARNING(
                'No Eagle credentials stored. Nothing will sync.'))
            self.stdout.write('  manage.py eagle_credential --set transcription')
            return

        self.stdout.write(self.style.MIGRATE_HEADING('Stored Eagle sessions'))
        for credential in credentials:
            style = (self.style.SUCCESS if credential.status == EagleCredential.ACTIVE
                     else self.style.WARNING)
            verified = (f'{credential.last_verified_at:%Y-%m-%d %H:%M}'
                        if credential.last_verified_at else 'never')
            login_state = 'configured' if credential.has_login else 'not-configured'
            self.stdout.write(style(
                f'  {credential.product:14} {credential.status:8} '
                f'login={login_state:14} '
                f'account={credential.account_label or "-":12} '
                f'minutes={credential.minutes_balance} verified={verified}'))
            if credential.last_failure_detail:
                self.stdout.write(f'      last failure: {credential.last_failure_detail[:120]}')
        # No flag prints the cookie. There is deliberately no such flag.
