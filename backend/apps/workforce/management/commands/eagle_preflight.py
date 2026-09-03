"""
Everything we can check about a task WITHOUT spending a minute.

WHY THIS EXISTS. Claiming is the moment money is spent and an irreversible
one-hour clock starts. Warming is free of Eagle minutes -- it costs provider
quota and nothing else -- so the entire document that would be submitted can
be rendered and inspected while the task is still sitting in the pool.

The order that protects you is therefore:

    warm  ->  preflight  ->  polish  ->  preflight again  ->  claim  ->  submit

By the time anything is claimed, you have already read the exact bytes.

CLAIMS NOTHING. SUBMITS NOTHING. SPENDS NO MINUTES. This command only
reads.

    manage.py eagle_preflight 4492
    manage.py eagle_preflight 4492 --show          # print the document
    manage.py eagle_preflight --ready              # every task worth a look
"""
import logging
import re

from django.core.management.base import BaseCommand, CommandError

from apps.workforce import states
from apps.workforce.models import Task

logger = logging.getLogger('eagle')

#: The header of a submission Eagle has ACCEPTED, as a shape. Taken from
#: real accepted documents, not from a specification -- there isn't one.
EXPECTED = {
    Task.TRANSCRIPTION: [
        (r'^Transcription Job: .+$', 'the job title line'),
        (r'^Client: Eagle Web$', 'the client line'),
        (r'^Transcribed By: .+$', 'who did the work'),
        (r'^Date: \d{2}/\d{2}/\d{4}$', 'the date, as DD/MM/YYYY'),
    ],
    Task.TRANSLATION: [
        # BARE IS CORRECT HERE. A transcription header names its subject;
        # a translation header does not -- the platform already shows the
        # task id beside it, so we emit "Translation Job:" and nothing more.
        # This pattern demanded a title after the colon and therefore failed
        # our own correct output. Accepted work exists both ways (task 4452
        # carried a filename), so the check is only that the line is there.
        (r'^Translation Job:', 'the job title line'),
        (r'^Client: Eagle Web$', 'the client line'),
        (r'^Translated By: .+$', 'who did the work'),
        (r'^Date: \d{2}/\d{2}/\d{4}$', 'the date, as DD/MM/YYYY'),
        (r'^Source Language: \w+', 'the source language, spelled out'),
        (r'^Target Language: \w+', 'the target language, spelled out'),
        (r'^Translation :$', 'the body marker'),
    ],
}

OK = '  [ ok ] '
WARN = '  [ ?? ] '
BAD = '  [FAIL] '


class Command(BaseCommand):
    help = 'Inspect what would be submitted for a task, without claiming it.'

    def add_arguments(self, parser):
        parser.add_argument('external_id', nargs='?',
                            help='The Eagle task id, e.g. 4492.')
        parser.add_argument('--ready', action='store_true',
                            help='List every task far enough along to check.')
        parser.add_argument('--show', action='store_true',
                            help='Print the rendered document in full.')

    def handle(self, *args, **options):
        if options['ready']:
            self._list_ready()
            return

        external_id = options['external_id']
        if not external_id:
            raise CommandError('Give an Eagle task id, or --ready to list them.')

        task = Task.objects.filter(external_task_id=str(external_id)).first()
        if task is None:
            raise CommandError(f'No task {external_id!r} in our database.')

        self._check(task, show=options['show'])

    # ------------------------------------------------------------------ #

    def _list_ready(self):
        from apps.transcriptions.models import AIRevision

        self.stdout.write('Tasks far enough along to inspect:\n')
        found = False
        for task in Task.objects.filter(
            status__in=states.CLAIMABLE_STATES, claimed_by__isnull=True,
            job__isnull=False, job__status='completed',
        ).order_by('duration_seconds'):
            translated = (
                task.kind != Task.TRANSLATION
                or task.deliverable_revision_id is not None
                or bool((task.polished_text or '').strip())
            )
            minutes = (task.duration_seconds or 0) // 60
            state = 'ready' if translated else 'not translated yet'
            self.stdout.write(
                f'  {task.external_task_id:8} {task.kind:13} {minutes:3}min  {state}')
            found = True
        if not found:
            self.stdout.write('  (none yet — the warm pool is still working)')

    def _check(self, task: Task, show: bool = False):
        from apps.workforce.eagle_claim import check_minutes, minutes_required_for
        from apps.workforce.eagle_submit import (
            SubmissionRefused, preview_submission,
        )

        w = self.stdout.write
        w('')
        w(f'TASK {task.external_task_id}  ({task.kind}, '
          f'{(task.duration_seconds or 0) // 60} minutes of audio)')
        w(f'  status: {task.status}   held by: '
          f'{task.claimed_by.worker_code if task.claimed_by_id else "nobody"}')
        w('')

        # -- can we even afford it -------------------------------------- #
        w('COST')
        required = minutes_required_for(task)
        try:
            check = check_minutes(task)
            line = (f'{OK}{required} minutes needed, {check.available} available'
                    if check.sufficient else
                    f'{BAD}{required} minutes needed, {check.available} available'
                    f' — a claim would be refused')
        except Exception as exc:  # pragma: no cover - diagnostic only
            line = f'{WARN}could not read the balance ({type(exc).__name__})'
        w(line)
        w('')

        # -- what would be sent ----------------------------------------- #
        w('THE DOCUMENT')
        try:
            preview = preview_submission(task)
        except SubmissionRefused as exc:
            w(f'{BAD}cannot be rendered yet: {exc.safe_message}')
            w('')
            w('  Nothing was claimed. Nothing was spent.')
            return
        except Exception as exc:
            w(f'{BAD}unexpected error rendering: {type(exc).__name__}: {exc}')
            return

        content = preview.content
        lines = content.splitlines()
        # TWO DIFFERENT COUNTS, and they should differ. `word_count` is the
        # whole document including our header; the "(Number Of Words: N)"
        # line counts only the body, which is what Eagle reads. Printing
        # them without saying so makes a correct document look wrong.
        w(f'{OK}renders: {len(content):,} characters, '
          f'{preview.word_count:,} words including the header')

        # -- header, line by line, against an accepted submission ------- #
        w('')
        w('HEADER (against a submission Eagle accepted)')
        head = [line for line in lines[:12] if line.strip()]
        for pattern, description in EXPECTED.get(task.kind, []):
            if any(re.match(pattern, line) for line in head):
                w(f'{OK}{description}')
            else:
                w(f'{BAD}{description} — MISSING or malformed')

        # -- the parts that differ by kind ------------------------------ #
        w('')
        w('BODY')
        if task.kind == Task.TRANSCRIPTION:
            stamps = re.findall(
                r'^\[\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}\]$',
                content, re.MULTILINE)
            speakers = set(re.findall(r'^(Male|Female)_\d+:$', content, re.MULTILINE))
            named = re.findall(r'^(?!Male_|Female_)([A-Z][\w ]{1,30}):$',
                               content, re.MULTILINE)
            w(f'{OK}{len(stamps)} timestamp lines'
              if stamps else f'{BAD}no timestamp lines — Eagle grades timing')
            w(f'{OK}speaker labels: {sorted(speakers) or "none"}'
              if speakers else f'{BAD}no Male_N/Female_N speaker labels')
            if named:
                w(f'{BAD}names used as speakers: {sorted(set(named))[:4]}')
            w(f'{OK}end-of-recording marker present'
              if '[END OF RECORDING' in content
              else f'{BAD}end-of-recording marker missing')
        else:
            body_match = re.search(r'\(Number Of Words: ([\d,]+)\)', content)
            if body_match:
                claimed = int(body_match.group(1).replace(',', ''))
                w(f'{OK}word count line: {claimed:,} words of body — this '
                  f'is the number Eagle reads')
            else:
                w(f'{BAD}word count line missing')
            if '-->' in content:
                w(f'{BAD}timestamps present — a translation must not have them')
            else:
                w(f'{OK}no timestamps, as a translation should be')
            polished = bool((task.polished_text or '').strip())
            w(f'{OK}submitting the POLISHED version' if polished else
              f'{WARN}submitting the RAW machine translation — no human has '
              f'polished it')

        # -- the gate ---------------------------------------------------- #
        w('')
        w('QUALITY GATE')
        if not preview.findings:
            w(f'{OK}nothing flagged')
        for finding in sorted(preview.findings,
                              key=lambda f: {'critical': 0, 'high': 1,
                                             'medium': 2, 'low': 3}
                              .get(f['severity'], 9)):
            mark = BAD if finding['severity'] == 'critical' else WARN
            w(f"{mark}{finding['severity']:8} {finding['message'][:90]}")

        # -- the verdict ------------------------------------------------- #
        w('')
        if preview.blocking:
            w('VERDICT: BLOCKED. Something critical must be fixed first.')
        elif any(f['severity'] == 'high' for f in preview.findings):
            w('VERDICT: submittable, but read the HIGH findings first — those '
              'are the ones that get work sent back.')
        else:
            w('VERDICT: looks submittable.')

        w('')
        w('  Nothing was claimed. Nothing was submitted. No minutes were spent.')

        if show:
            w('')
            w('=' * 70)
            w(content)
            w('=' * 70)
