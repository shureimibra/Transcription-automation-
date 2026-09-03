"""
Bootstrap accounts without the Django admin.

Exists because the first administrator has to come from somewhere, and
because "six workers" is small enough that account creation is an operator
task rather than a self-service flow. Passwords are prompted for, never
passed as an argument -- a password on the command line lands in the shell
history and in the process list.
"""
from getpass import getpass

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.workforce import roles
from apps.workforce.models import Account, WorkerProfile


class Command(BaseCommand):
    help = 'Create a worker (or administrator) account and its profile.'

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--code', help='Worker code, e.g. W001')
        parser.add_argument('--name', default='', help='Display name')
        parser.add_argument('--email', default='')
        parser.add_argument('--admin', action='store_true',
                            help='Grant administrator rights.')
        parser.add_argument('--max-tasks', type=int, default=3)

    def handle(self, *args, **options):
        username = options['username']
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(f'User {username!r} already exists.')

        code = options.get('code') or (
            'ADMIN' if options['admin'] else None
        )
        if not code:
            raise CommandError('--code is required for a worker account.')
        if WorkerProfile.objects.filter(worker_code__iexact=code).exists():
            raise CommandError(f'Worker code {code!r} is already in use.')

        password = getpass('Password: ')
        if password != getpass('Password (again): '):
            raise CommandError('Passwords did not match.')
        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError('\n'.join(exc.messages))

        with transaction.atomic():
            user = User.objects.create_user(
                username=username, email=options['email'], password=password,
                is_staff=options['admin'], is_superuser=options['admin'],
            )
            # An explicit role row, not an inference. Both accounts this
            # command makes are in the workforce; a tool-only account is
            # created through the admin surface instead.
            account_role = roles.ADMIN if options['admin'] else roles.TASKER
            Account.objects.create(user=user, role=account_role)
            profile = WorkerProfile.objects.create(
                user=user, worker_code=code,
                display_name=options['name'],
                max_concurrent_tasks=options['max_tasks'],
            )

        self.stdout.write(self.style.SUCCESS(
            f'Created {roles.label(account_role).lower()} {username!r} '
            f'with code {profile.worker_code}.'
        ))
