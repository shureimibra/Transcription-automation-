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
"""
import sys

from django.core.management.base import BaseCommand, CommandError

from apps.workforce.credentials import (
    PRODUCT_CHOICES, TRANSCRIPTION, TRANSLATION, EagleCredential,
)


class Command(BaseCommand):
    help = "Store, inspect or verify the platform's Eagle session cookies."

    def add_arguments(self, parser):
        parser.add_argument('--set', metavar='PRODUCT',
                            choices=[TRANSCRIPTION, TRANSLATION],
                            help='Read a cookie header from stdin and store it.')
        parser.add_argument('--check', metavar='PRODUCT',
                            choices=[TRANSCRIPTION, TRANSLATION],
                            help='Ask the platform whether the session still works.')
        parser.add_argument('--show', action='store_true',
                            help='Status only. Never prints cookie material.')

    def handle(self, *args, **options):
        if options['set']:
            return self._store(options['set'])
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
            self.stdout.write(style(
                f'  {credential.product:14} {credential.status:8} '
                f'account={credential.account_label or "-":12} '
                f'minutes={credential.minutes_balance} verified={verified}'))
            if credential.last_failure_detail:
                self.stdout.write(f'      last failure: {credential.last_failure_detail[:120]}')
        # No flag prints the cookie. There is deliberately no such flag.
