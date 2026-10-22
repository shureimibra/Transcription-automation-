"""
Outside Eagle taskers: find a task by its exact ID, work a private copy.

WHO THIS IS FOR. Eagle workers who are not part of our workforce. They
claim a task on Eagle themselves, copy its ID from the Eagle URL, and look
it up here. Our own Eagle account has usually already pulled that task and
prepared it (Whisper transcript, machine translation), so they start from a
finished draft instead of a blank page. They edit, run the checks, copy the
final draft and paste it into Eagle. Nothing here submits anything.

NOTHING HERE TOUCHES THE SHARED DRAFT. Every edit lands on an
EagleTaskerCopy row owned by the tasker. The task, its job and its
deliverable revision are only ever read -- see EagleTaskerCopy's docstring.

THE 70-MINUTE WINDOW. A task disappears from Eagle's pool the moment anyone
claims it, and sync retires it (status CANCELLED, `cancelled_at` set). The
tasker who just claimed it is exactly the person who needs it next, so a
retired task stays findable for FINDABLE_MINUTES after it left -- with a
warning, not a refusal, because this system cannot tell whether the person
searching is the one who claimed it.

REUSING THE CHECKS AND THE FORMATTER. The quality gate and the Eagle
renderer read a Task. `CopyView` below is a Task with the copy's text laid
over it, so both run unchanged against what the tasker actually wrote.
"""
import logging
import os
import re
import threading
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Optional

from django.db.models import F
from django.utils import timezone

from . import states
from .models import EagleTaskerCopy, Task

logger = logging.getLogger('workforce')

#: How long a task stays findable after it left Eagle's pool.
FINDABLE_MINUTES = int(os.environ.get('EAGLE_TASKER_WINDOW_MINUTES', '70'))

#: How long a tasker can keep working a copy after first opening it, even
#: once the task itself is no longer findable by others.
COPY_HOURS = int(os.environ.get('EAGLE_TASKER_COPY_HOURS', '3'))

#: Ceilings on what a tasker can write into their copy. Far above any real
#: task; low enough that the edit endpoints cannot be used to bloat the
#: database.
MAX_LINE_CHARS = 20000
MAX_ROWS = 5000
MAX_SPEAKERS = 100

#: The name line in the final draft's header. The tasker replaces it with
#: their own name -- the document goes into Eagle under their account.
NAME_PLACEHOLDER = '[Your name]'

# Readiness, as the tasker's screen shows it.
NOT_STARTED = 'not_started'
TRANSCRIBING = 'transcribing'
ENGLISH_READY = 'english_ready'   # translation task: English done, French not
READY = 'ready'
FAILED = 'failed'


class NotFindable(Exception):
    """No task with that ID is available to an Eagle tasker right now."""

    def __init__(self, message):
        super().__init__(message)
        self.safe_message = message


# --------------------------------------------------------------------- #
# Finding a task
# --------------------------------------------------------------------- #

_TRANSCRIBE_HOST = re.compile(r'transcrib', re.I)
_TRANSLATE_HOST = re.compile(r'translat', re.I)


def parse_task_reference(raw: str):
    """
    (external_id, kind or None) from whatever the tasker pasted.

    Accepts the bare ID or the whole Eagle URL. A URL also says WHICH product
    it came from (transcribe.… vs translation.…), which matters because the
    two products number their tasks independently and could one day collide.
    """
    text = str(raw or '').strip()
    kind = None
    if '/' in text or '.' in text:
        if _TRANSLATE_HOST.search(text):
            kind = Task.TRANSLATION
        elif _TRANSCRIBE_HOST.search(text):
            kind = Task.TRANSCRIPTION
        numbers = re.findall(r'\d+', text.split('?')[0])
        text = numbers[-1] if numbers else ''
    if not re.fullmatch(r'\d{1,12}', text):
        return '', kind
    return text, kind


def is_findable(task: Task, now=None) -> bool:
    """In Eagle's pool now, or left it less than FINDABLE_MINUTES ago."""
    now = now or timezone.now()
    if task.status == states.NEW:
        return True
    if task.status == states.CANCELLED and task.cancelled_at:
        return task.cancelled_at >= now - timedelta(minutes=FINDABLE_MINUTES)
    return False


def copy_is_open(copy: Optional[EagleTaskerCopy], now=None) -> bool:
    """A tasker who already opened the task keeps it for COPY_HOURS."""
    if copy is None:
        return False
    now = now or timezone.now()
    return copy.opened_at >= now - timedelta(hours=COPY_HOURS)


def find_task(raw_reference: str, user) -> Task:
    """
    The one task this reference names, if this tasker may open it.

    EXACT ID ONLY. There is no listing and no partial match: an outside
    account should only ever reach a task it already knows the ID of,
    which in practice means one it already holds on Eagle.
    """
    external_id, kind = parse_task_reference(raw_reference)
    if not external_id:
        raise NotFindable('Paste the task link, or its ID.')

    candidates = Task.objects.filter(external_task_id=external_id).exclude(external_source='')
    if kind:
        candidates = candidates.filter(kind=kind)

    now = timezone.now()
    reachable = [
        task for task in candidates.order_by('-created_at')
        if is_findable(task, now) or copy_is_open(
            EagleTaskerCopy.objects.filter(task=task, owner=user).first(), now)
    ]
    if not reachable:
        raise NotFindable(
            f'Task {external_id} is not available here. Check the ID, or it '
            f'may have been claimed more than {FINDABLE_MINUTES} minutes ago.'
        )
    if len({task.kind for task in reachable}) > 1:
        raise NotFindable(
            f'Both a transcription and a translation task use the ID '
            f'{external_id}. Paste the whole task link instead.'
        )
    return reachable[0]


def task_for(pk, user) -> Task:
    """The task behind a tasker URL, held to the same rule as a lookup."""
    task = Task.objects.filter(pk=pk).exclude(external_source='').first()
    if task is None:
        raise NotFindable('That task is not available.')
    copy = EagleTaskerCopy.objects.filter(task=task, owner=user).first()
    if not (is_findable(task) or copy_is_open(copy)):
        raise NotFindable(
            'This task is no longer available here. Tasks stay open for '
            f'{COPY_HOURS} hours after you first open them.'
        )
    return task


def readiness(task: Task) -> str:
    job = task.job
    if job is None:
        return NOT_STARTED
    if job.status == 'completed':
        if task.kind == Task.TRANSLATION and not task.deliverable_revision_id:
            return ENGLISH_READY
        return READY
    if job.status in ('failed', 'cancelled'):
        return FAILED
    return TRANSCRIBING


# --------------------------------------------------------------------- #
# Transcribing on demand
# --------------------------------------------------------------------- #

#: Whisper is CPU-heavy. One on-demand run at a time, on top of the warm
#: pool, is what this server can carry without slowing everyone down.
_on_demand = threading.Semaphore(1)
_starting = set()
_starting_lock = threading.Lock()


def start_transcription(task: Task) -> bool:
    """
    Start the free half of warming (fetch + Whisper, no provider calls) for
    a task nobody has transcribed yet. True if a run was started.

    Runs on a background thread so the lookup answers immediately; the
    tasker's screen polls readiness.
    """
    if task.job_id is not None:
        return False
    with _starting_lock:
        if task.id in _starting:
            return False
        _starting.add(task.id)

    def run():
        from django.db import connection
        from . import warmpool
        try:
            with _on_demand:
                fresh = Task.objects.get(pk=task.pk)
                if fresh.job_id is None:
                    warmpool.warm_task(fresh, allow_ai=False)
        except Exception:
            logger.exception('[TASKER] on-demand transcription failed for %s',
                             task.external_task_id)
        finally:
            with _starting_lock:
                _starting.discard(task.id)
            connection.close()

    threading.Thread(target=run, name=f'tasker-asr-{task.external_task_id}',
                     daemon=True).start()
    return True


# --------------------------------------------------------------------- #
# The private copy
# --------------------------------------------------------------------- #

def _segment_row(seg) -> dict:
    return {
        'id': str(seg.id),
        'start_time': float(seg.start_time),
        'end_time': float(seg.end_time),
        'speaker_label': seg.speaker_label or '',
        'text': seg.text or '',
        'original_text': seg.original_text,
    }


def copy_for(task: Task, user) -> EagleTaskerCopy:
    """
    This tasker's copy of this task, created on first open.

    The transcript is read in once, the first time it is ready. Later
    changes to the shared draft do not reach an existing copy: the tasker
    is editing their own document, and having it rewritten under them
    would be the overwrite this module exists to prevent.
    """
    copy, _ = EagleTaskerCopy.objects.get_or_create(task=task, owner=user)
    if copy.snapshot_at is None and task.job_id and task.job.status == 'completed':
        copy.segments = [_segment_row(seg)
                         for seg in task.job.segments.all().order_by('start_time')]
        copy.speaker_aliases = dict(task.job.speaker_aliases or {})
        copy.snapshot_at = timezone.now()
        copy.save(update_fields=['segments', 'speaker_aliases', 'snapshot_at', 'updated_at'])
    return copy


def segment_payload(row: dict) -> dict:
    """A copy row in the same shape the review screen gets for a real one."""
    return {
        'id': row['id'],
        'start_time': row['start_time'],
        'end_time': row['end_time'],
        'speaker_label': row.get('speaker_label', ''),
        'text': row.get('text', ''),
        'is_edited': row.get('original_text') is not None,
    }


def transcript_payload(task: Task, copy: EagleTaskerCopy) -> dict:
    rows = sorted(copy.segments, key=lambda r: r['start_time'])
    return {
        'id': str(copy.id),
        'status': 'completed' if copy.snapshot_at else 'processing',
        'segments': [segment_payload(r) for r in rows],
        'speaker_aliases': copy.speaker_aliases or {},
        # ONE FIELD, NOT THE WHOLE DICT: document_metadata can also carry
        # owner/title details that are none of an outside tasker's business.
        'document_metadata': {
            'uncertain_speakers':
                (task.job.document_metadata or {}).get('uncertain_speakers', [])
                if task.job_id else [],
        },
    }


def _find_row(copy, segment_id):
    for row in copy.segments:
        if row['id'] == str(segment_id):
            return row
    raise NotFindable('That line is not in your copy.')


def edit_segment(copy, segment_id, changes: dict) -> dict:
    """Same rules as EditSegmentView: text and speaker only, never timing."""
    row = _find_row(copy, segment_id)
    if 'text' in changes:
        text = str(changes['text'] if changes['text'] is not None else '')
        if len(text) > MAX_LINE_CHARS:
            raise ValueError('That line is far longer than any real line should be.')
        if row.get('original_text') is None and text != row['text']:
            row['original_text'] = row['text']
        row['text'] = text
    if 'speaker_label' in changes:
        row['speaker_label'] = str(changes['speaker_label'] or '').strip()[:100]
    copy.save(update_fields=['segments', 'updated_at'])
    return segment_payload(row)


def revert_segment(copy, segment_id) -> dict:
    row = _find_row(copy, segment_id)
    if row.get('original_text') is None:
        raise ValueError('This line has not been edited -- there is nothing to revert.')
    row['text'] = row['original_text']
    row['original_text'] = None
    copy.save(update_fields=['segments', 'updated_at'])
    return segment_payload(row)


def split_segment(copy, segment_id, split_at, second_speaker) -> dict:
    """Same estimate as SplitSegmentView: the boundary is placed by character share."""
    row = _find_row(copy, segment_id)
    text = row['text'] or ''
    second_speaker = str(second_speaker or '').strip()[:100]
    if not isinstance(split_at, int) or not (0 < split_at < len(text)):
        raise ValueError("split_at must be a position strictly inside the line's text.")
    if not second_speaker:
        raise ValueError('second_speaker is required.')
    if len(copy.segments) >= MAX_ROWS:
        raise ValueError('This transcript cannot be split any further.')
    first_text, second_text = text[:split_at].rstrip(), text[split_at:].lstrip()
    if not first_text or not second_text:
        raise ValueError('Both halves of the split must contain text.')

    original_end = row['end_time']
    split_time = row['start_time'] + (split_at / len(text)) * (original_end - row['start_time'])
    row.update(text=first_text, end_time=split_time, original_text=None)
    second = {
        'id': str(uuid.uuid4()), 'start_time': split_time, 'end_time': original_end,
        'speaker_label': second_speaker, 'text': second_text, 'original_text': None,
    }
    copy.segments.append(second)
    copy.segments.sort(key=lambda r: r['start_time'])
    copy.save(update_fields=['segments', 'updated_at'])
    return {'first': segment_payload(row), 'second': segment_payload(second)}


def set_speaker_aliases(copy, aliases: dict) -> None:
    """Merges like the job endpoint: an empty name reverts that speaker."""
    if len(aliases) > MAX_SPEAKERS:
        raise ValueError('Too many speakers in one rename.')
    updated = dict(copy.speaker_aliases or {})
    for raw_label, alias in aliases.items():
        if alias:
            updated[str(raw_label)] = str(alias)[:100]
        else:
            updated.pop(str(raw_label), None)
    if len(updated) > MAX_SPEAKERS:
        raise ValueError('Too many speakers.')
    copy.speaker_aliases = {str(k)[:100]: v for k, v in updated.items()}
    copy.save(update_fields=['speaker_aliases', 'updated_at'])


def record_applied_finding(copy, phrase, suggestion) -> None:
    copy.applied_findings = [
        *copy.applied_findings,
        {'phrase': str(phrase)[:500], 'suggestion': str(suggestion)[:500],
         'occurred_at': timezone.now().isoformat()},
    ]
    copy.save(update_fields=['applied_findings', 'updated_at'])


# --------------------------------------------------------------------- #
# The task, as seen through one copy
# --------------------------------------------------------------------- #

class _CopyRows(list):
    """Enough of a queryset for the renderer: .all() and .order_by()."""

    def all(self):
        return self

    def order_by(self, *_):
        return _CopyRows(sorted(self, key=lambda r: r.start_time))


class CopyView:
    """
    A Task with the tasker's copy laid over it.

    The quality gate and render_for_eagle read `task.job.segments`,
    `task.polished_text` and the two self-check timestamps. Those come from
    the copy; everything else (kind, project, languages, the machine
    translation) is read straight through from the real task.
    """

    def __init__(self, task: Task, copy: EagleTaskerCopy):
        self._task = task
        self._copy = copy
        rows = _CopyRows(
            SimpleNamespace(
                id=r['id'], start_time=r['start_time'], end_time=r['end_time'],
                speaker_label=r.get('speaker_label', ''), text=r.get('text', ''),
                original_text=r.get('original_text'),
            )
            for r in copy.segments
        )
        job = task.job
        self.job = SimpleNamespace(
            id=job.id, segments=rows, speaker_aliases=copy.speaker_aliases or {},
            enable_diarization=job.enable_diarization,
        ) if job is not None and copy.snapshot_at else None
        self.polished_text = copy.polished_text
        self.polished_at = copy.polished_at
        self.accuracy_check_notes = copy.accuracy_check_notes
        self.accuracy_check_at = copy.accuracy_check_at

    def __getattr__(self, name):
        return getattr(self._task, name)


def final_draft(task: Task, copy: EagleTaskerCopy):
    """
    The document to paste into Eagle, and what the checks think of it.

    The placeholder itself looks like a bracket tag to the transcript
    checks ("[Your name]" is not one of the agreed tags). That finding is
    about our header, not the tasker's work, so it is dropped.
    """
    from .eagle_submit import preview_submission
    draft = preview_submission(CopyView(task, copy), submitter=NAME_PLACEHOLDER)
    draft.findings = [f for f in draft.findings
                      if NAME_PLACEHOLDER not in str(f.get('message', ''))]
    return draft


# --------------------------------------------------------------------- #
# Re-translating one line
# --------------------------------------------------------------------- #

def retranslate_line(task: Task, copy: EagleTaskerCopy, index: int, user) -> str:
    """
    One line of the tasker's own English, translated again. One provider
    request, counted on the copy and in the AI usage ledger under this user.

    Not an AIRevision on the shared job: the English being translated is the
    tasker's edited copy, which the shared job has never seen.
    """
    from apps.transcriptions import ai_budget
    from apps.transcriptions.models import AIUsageRecord
    from apps.transcriptions.tasks import AI_CONCURRENCY_SEMAPHORE, _ai_language_name
    from core.services.ai_assist import is_configured
    from core.services.ai_assist.processor import AIPostProcessor

    rows = sorted(copy.segments, key=lambda r: r['start_time'])
    if not (0 <= index < len(rows)):
        raise ValueError('That line is not in your copy.')
    english = (rows[index].get('text') or '').strip()
    if not english:
        raise ValueError('That line is empty.')
    if not is_configured():
        raise ValueError('Re-translate is not available on this server right now.')

    ai_budget.check_rate_limit()
    ai_budget.check_daily_requests(1)

    with AI_CONCURRENCY_SEMAPHORE:
        batches = list(AIPostProcessor().iter_batches(
            [SimpleNamespace(text=english)], 'translate',
            target_language=_ai_language_name(task.target_language or 'fr'),
            source_language=task.source_language or 'en',
        ))
    if not batches or not batches[0].segments:
        raise ValueError('The translation did not come back. Try again.')

    batch = batches[0]
    if batch.cache_misses:
        AIUsageRecord.objects.create(
            job=task.job, owner=user, operation='translate',
            model_name=batch.model_name or '', request_count=1,
            prompt_tokens=batch.usage.prompt_tokens,
            completion_tokens=batch.usage.completion_tokens,
            total_tokens=batch.usage.total_tokens,
        )
    EagleTaskerCopy.objects.filter(pk=copy.pk).update(
        retranslate_count=F('retranslate_count') + 1, updated_at=timezone.now())
    return batch.segments[0].processed_text
