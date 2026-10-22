"""
manage.py tasker_check -- can a tasker find every task in the pool right now?

Lists every task currently in the pool (and every one that left it within
the lookup window), runs each one's ID through the exact lookup a tasker's
search box uses, and says how ready its draft is. Read-only: it creates no
copies and changes nothing.

Run it on the server after deploying, and whenever a tasker says "my task
is not found".
"""
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.workforce import eagle_tasker, states
from apps.workforce.models import Task


class Command(BaseCommand):
    help = 'Check that every task in the pool can be found by the tasker search.'

    def handle(self, *args, **options):
        since = timezone.now() - timezone.timedelta(minutes=eagle_tasker.FINDABLE_MINUTES)
        tasks = (Task.objects.exclude(external_source='')
                 .filter(Q(status=states.NEW) | Q(status=states.CANCELLED, cancelled_at__gte=since))
                 .select_related('job').order_by('kind', 'external_task_id'))

        counts, failures = {}, []
        self.stdout.write(f'{"ID":>8}  {"kind":<13} {"where":<11} readiness')
        for task in tasks:
            try:
                # No user: nobody's copy keeps a task open, only the pool rule decides.
                found = eagle_tasker.find_task(task.external_task_id, None)
                ok = found.pk == task.pk
            except eagle_tasker.NotFindable as exc:
                ok, found = False, exc.safe_message
            if not ok:
                # Same ID used by both products: the full link disambiguates.
                link = ('https://translation.x/tasks/' if task.kind == Task.TRANSLATION
                        else 'https://transcribe.x/task/') + task.external_task_id
                try:
                    ok = eagle_tasker.find_task(link, None).pk == task.pk
                except eagle_tasker.NotFindable:
                    pass
            ready = eagle_tasker.readiness(task)
            counts[ready] = counts.get(ready, 0) + 1
            where = 'in pool' if task.status == states.NEW else 'claimed'
            mark = '' if ok else '   <-- NOT FINDABLE'
            if not ok:
                failures.append(task.external_task_id)
            self.stdout.write(f'{task.external_task_id:>8}  {task.kind:<13} {where:<11} {ready}{mark}')

        total = sum(counts.values())
        self.stdout.write('')
        self.stdout.write(f'{total} task(s) searchable now: '
                          + ', '.join(f'{n} {r}' for r, n in sorted(counts.items())))
        if failures:
            self.stdout.write(self.style.ERROR(f'NOT findable: {", ".join(failures)}'))
        else:
            self.stdout.write(self.style.SUCCESS('Every task above can be found by its ID or link.'))

