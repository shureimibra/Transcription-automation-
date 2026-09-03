"""
Day-to-day account administration: who exists, who can work, who forgot
their password.

WHY THIS EXISTS ALONGSIDE create_worker. That command bootstraps an account
once. Everything after that -- a forgotten password, a tasker who leaves,
one who comes back -- is a recurring operator job, and the alternative is
the Django admin or a shell session with `set_password` typed by hand. On a
six-person team the person doing this is the operator, not an engineer, and
"open a Python shell and import the user model" is how accounts end up
mismanaged.

DISABLING HAS TO ACTUALLY STOP THE WORK. `is_active_worker` requires BOTH
gates: the Django account active AND the profile not suspended. This command
closes both, because closing one and believing the person is locked out is
the failure that matters -- they would still hold whatever they had claimed
and could still sign in.

PASSWORDS ARE PROMPTED FOR, NEVER PASSED AS AN ARGUMENT. A password in argv
lands in the shell history and in the process list, where every other
account on the machine can read it. Same rule as create_worker and
eagle_credential.
"""
from getpass import getpass

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.workforce import services, states
from apps.workforce.models import Task, WorkerProfile


class Command(BaseCommand):
    help = ('Manage worker accounts: list, password, disable, enable. '
            'Run with no action to list everyone.')

    def add_arguments(self, parser):
        parser.add_argument(
            'action', nargs='?', default='list',
            choices=['list', 'password', 'disable', 'enable'],
            help='What to do. Default: list.')
        parser.add_argument(
            'username', nargs='?',
            help='Whose account. Not needed for "list".')
        parser.add_argument(
            '--as', dest='acting_as', default='',
            help='Which administrator is doing this. Needed for "disable" '
                 'when more than one administrator exists, so the audit '
                 'trail names a person rather than guessing.')

    def handle(self, *args, **options):
        action = options['action']
        if action == 'list':
            return self._list()

        username = (options.get('username') or '').strip()
        if not username:
            raise CommandError(
                f'Which account? Usage: manage.py worker {action} <username>')

        user = User.objects.filter(username=username).first()
        if user is None:
            # Naming the mistake is right here: this is an operator at a
            # console managing their own team, not a login form where
            # confirming an account exists would help an attacker.
            raise CommandError(f'No account named {username!r}. '
                               f'Run "manage.py worker list" to see them all.')

        return getattr(self, f'_{action}')(user, options)

    # ------------------------------------------------------------------ #

    def _list(self):
        profiles = {p.user_id: p for p in WorkerProfile.objects.select_related('user')}
        users = User.objects.order_by('username')
        if not users:
            self.stdout.write('No accounts yet. Create one with create_worker.')
            return

        self.stdout.write(f'{"USERNAME":<18}{"CODE":<10}{"ROLE":<8}'
                          f'{"CAN WORK":<10}{"HOLDING":<9}LAST SEEN')
        for user in users:
            profile = profiles.get(user.id)
            role = 'admin' if user.is_superuser or user.is_staff else 'tasker'
            # THE COLUMN THAT MATTERS is not the profile status by itself:
            # either gate closed means no work, so report the answer rather
            # than the two inputs to it.
            can_work = 'yes' if (profile and profile.is_active_worker) else 'NO'
            holding = Task.objects.filter(
                claimed_by=profile, status__in=states.WORKER_HELD_STATES,
            ).count() if profile else 0
            seen = profile.last_activity_at.strftime('%Y-%m-%d %H:%M') if (
                profile and profile.last_activity_at) else '-'
            self.stdout.write(
                f'{user.username:<18}{(profile.worker_code if profile else "-"):<10}'
                f'{role:<8}{can_work:<10}{holding:<9}{seen}')

    def _password(self, user, options=None):
        """Set a new password. The commonest job here, so it is the simplest."""
        password = getpass(f'New password for {user.username}: ')
        if password != getpass('Again: '):
            raise CommandError('Passwords did not match. Nothing was changed.')
        try:
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError('\n'.join(exc.messages))

        user.set_password(password)
        user.save(update_fields=['password'])
        self.stdout.write(self.style.SUCCESS(
            f'Password set for {user.username}. '
            f'Their existing sessions still work until they sign out.'))

    def _actor(self, options):
        """
        WHICH ADMINISTRATOR IS DOING THIS, and why it cannot be nobody.

        Releasing a claimed task is restricted to ROLE_ADMIN or the worker
        who holds it -- deliberately, so that no automated process can take
        work off a person. Passing `actor=None` means ROLE_SYSTEM, which the
        state machine refuses; the first version of this command did exactly
        that, and every release failed silently and was counted as "could
        not be released".

        The fix is a truthful actor, not a wider transition table. Widening
        it would let any system process release somebody's work, which is
        the protection being relied on here.
        """
        named = (options or {}).get('acting_as') or ''
        admins = User.objects.filter(is_superuser=True, is_active=True)
        if named:
            actor = admins.filter(username=named).first()
            if actor is None:
                raise CommandError(f'{named!r} is not an active administrator.')
            return actor
        if admins.count() == 1:
            return admins.first()
        if not admins.exists():
            raise CommandError(
                'No active administrator account exists, so this cannot be '
                'recorded against anybody. Create one with create_worker '
                '--admin.')
        raise CommandError(
            'More than one administrator exists. Say which one is doing '
            'this: manage.py worker disable <username> --as <admin>')

    def _disable(self, user, options=None):
        """
        Stop this person working, both gates at once, and free their queue.

        WORK IN HAND IS RELEASED, NOT LEFT SITTING. Holding on to it looks
        like the careful choice and is the opposite: a suspended worker's
        claimed tasks are reachable by nobody, so the one-hour clock simply
        runs out and the work is lost along with the minutes it was bought
        with. Releasing puts each task back in the pool where somebody else
        can still finish it.

        This is what the `deactivate` endpoint has always done. The two
        surfaces must not disagree about what suspending someone means.
        """
        profile = WorkerProfile.objects.filter(user=user).first()
        # Resolved BEFORE anything is written, so a missing administrator
        # stops the command rather than half-completing it.
        actor = self._actor(options) if profile else None

        with transaction.atomic():
            user.is_active = False
            user.save(update_fields=['is_active'])
            if profile is not None:
                profile.status = WorkerProfile.SUSPENDED
                profile.save(update_fields=['status'])

        released, stuck = 0, 0
        if profile is not None:
            for task in Task.objects.filter(
                claimed_by=profile, status__in=states.WORKER_HELD_STATES,
            ):
                try:
                    services.release_task(
                        task.id, actor=actor,
                        reason=f'{user.username} was disabled.')
                    released += 1
                except states.IllegalTransition:
                    # Already moved on -- submitted between the query and
                    # here. Nothing to free, and nothing wrong.
                    stuck += 1

        self.stdout.write(self.style.SUCCESS(
            f'{user.username} can no longer sign in or take work.'))
        if released:
            self.stdout.write(
                f'Released {released} task(s) back to the queue for someone else.')
        if stuck:
            self.stdout.write(self.style.WARNING(
                f'{stuck} task(s) could not be released — check them by hand.'))

    def _enable(self, user, options=None):
        """Let them back in. The exact inverse of disable."""
        profile = WorkerProfile.objects.filter(user=user).first()
        with transaction.atomic():
            user.is_active = True
            user.save(update_fields=['is_active'])
            if profile is not None:
                profile.status = WorkerProfile.ACTIVE
                profile.save(update_fields=['status'])

        if profile is None:
            self.stdout.write(self.style.WARNING(
                f'{user.username} can sign in, but has no worker profile, so '
                f'cannot take tasks. Create one with create_worker.'))
            return
        self.stdout.write(self.style.SUCCESS(
            f'{user.username} can sign in and take work again.'))
