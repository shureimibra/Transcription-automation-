"""
The endpoints a tasker's screen actually calls.

WHAT MAKES THESE DIFFERENT from the Phase 3 task endpoints: they are built
around one hour on a clock. A worker presses "start next", gets a task that
was transcribed before it was claimed, reviews it against the audio, and
submits. Everything here serves that loop and nothing else.

MEDIA IS PROXIED, ALWAYS. `/media/` streams audio from OUR cache through OUR
authorization, with range support so seeking works. A worker never receives an
Eagle URL, never receives a cookie, and cannot tell which external platform
the audio came from. That holds even for transcription audio, which Eagle
serves publicly -- the boundary is about who we are willing to expose, not
about what happens to be reachable.

NOTHING HERE DECIDES ANYTHING TWICE. Claiming goes through eagle_claim,
submitting through eagle_submit, state through services. These are a surface,
not a second implementation.
"""
import logging
import os
import re
import threading
from collections import defaultdict

from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from rest_framework import status, views
from rest_framework.response import Response

from . import services, states, warmpool, worker_prompts
from .services import record_event
from .eagle_claim import ClaimRefused, InsufficientMinutes, claim_for_worker
from .eagle_submit import (
    SubmissionFailed, SubmissionRefused, preview_submission, submit_task,
)
from .models import Task, TaskEvent, ready_for_work_q
from .permissions import (
    IsAuthenticatedWorkerOrAdmin, is_admin, training_mode_active, worker_profile,
)
from .serializers import TaskSerializer
from .views import AUTH_CLASSES

logger = logging.getLogger('workforce')

#: Streamed in chunks so a 30 MB file never sits in memory per listener.
STREAM_CHUNK = 64 * 1024


class StartNextTaskView(views.APIView):
    """
    POST /workforce/work/next/ -- "give me something to do".

    Prefers a WARM task: one already transcribed, so the worker's hour is
    spent reviewing rather than watching a progress bar. Falls back to a cold
    task only if asked, because claiming something unprocessed starts the
    clock on work the machine has not begun.
    """

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAuthenticatedWorkerOrAdmin]

    def post(self, request):
        profile = worker_profile(request.user)
        if profile is None:
            return Response({'error': 'Only workers can take tasks.'},
                            status=status.HTTP_403_FORBIDDEN)

        allow_cold = bool(request.data.get('allow_unprocessed'))
        candidate = self._pick(allow_cold)
        if candidate is None:
            return Response(
                {'error': 'No work is ready just yet. Try again in a moment.',
                 'code': 'pool_empty'},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            claimed = claim_for_worker(candidate.id, profile, request.user)
        except InsufficientMinutes as exc:
            # An operator problem. The worker is told plainly and is not
            # left thinking they did something wrong.
            logger.error('[WORK] minutes exhausted: %s', exc.detail)
            return Response({'error': exc.safe_message, 'code': 'no_minutes'},
                            status=status.HTTP_409_CONFLICT)
        except ClaimRefused as exc:
            return Response({'error': exc.safe_message, 'code': 'claim_refused'},
                            status=status.HTTP_409_CONFLICT)

        return Response(TaskSerializer(claimed, context={'request': request}).data)

    @staticmethod
    def _pick(allow_cold: bool):
        warm = Task.objects.filter(
            status=states.NEW, claimed_by__isnull=True,
            job__isnull=False, job__status='completed',
        ).order_by('duration_seconds', 'created_at').first()
        if warm or not allow_cold:
            return warm
        return Task.objects.filter(
            status=states.NEW, claimed_by__isnull=True,
        ).order_by('duration_seconds', 'created_at').first()


class TaskMediaView(views.APIView):
    """
    GET /workforce/tasks/<id>/media/ -- the audio, through us.

    RANGE REQUESTS ARE THE POINT. A reviewer clicks a line to hear that
    moment; without `Range` the browser would refetch 30 MB to seek two
    minutes in. This honours a single range, which is what audio elements
    send.
    """

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAuthenticatedWorkerOrAdmin]
    # Each miss fetches a whole audio file from Eagle. A worker listening
    # to their tasks touches this a few times an hour; a loop would pull
    # gigabytes and hammer the platform we depend on.
    throttle_scope = 'media'

    #: Only one download per task at a time. Two workers opening the same
    #: card would otherwise each start a fetch, and the second would write
    #: over the first mid-stream.
    _fetch_locks = defaultdict(threading.Lock)

    def get(self, request, pk):
        task = self._task_for(request, pk)
        path = warmpool.cache_path_for(task)

        if not os.path.exists(path) or os.path.getsize(path) == 0:
            # FETCH IT NOW.
            #
            # Audio used to appear only for the two or three tasks the warm
            # pool happened to be working on, so "Listen first" -- the whole
            # point of which is choosing between the fifty tasks on offer --
            # answered "not downloaded yet" for nearly all of them.
            #
            # Downloading is the cheap half of warming: it spends no Eagle
            # minutes and no provider quota, only bandwidth and disk. A
            # 16 MB file took 8.5 seconds when measured. Doing it on demand
            # means we fetch exactly what somebody actually wants to hear,
            # rather than pre-fetching fifty files on the chance.
            if not self._fetch(task, path):
                return Response(
                    {'error': 'The audio for this task could not be fetched.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        size = os.path.getsize(path)
        range_header = request.META.get('HTTP_RANGE', '')
        match = re.match(r'bytes=(\d+)-(\d*)', range_header or '')

        if not match:
            response = FileResponse(open(path, 'rb'), content_type='audio/mpeg')
            response['Content-Length'] = str(size)
            response['Accept-Ranges'] = 'bytes'
            return response

        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else size - 1
        end = min(end, size - 1)
        if start >= size:
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{size}'
            return response

        length = end - start + 1

        def chunks():
            with open(path, 'rb') as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    data = handle.read(min(STREAM_CHUNK, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        response = StreamingHttpResponse(chunks(), status=206,
                                         content_type='audio/mpeg')
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
        response['Content-Length'] = str(length)
        response['Accept-Ranges'] = 'bytes'
        return response


    @classmethod
    def _fetch(cls, task, path) -> bool:
        """
        Download this task's audio, once, now. True when it is on disk.

        Never raises: a worker pressing play on a task whose media has gone
        should be told there is no audio, not shown a server error.
        """
        with cls._fetch_locks[str(task.pk)]:
            # Another thread may have finished while we waited for the lock.
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return True
            try:
                warmpool.fetch_media(task)
            except Exception as exc:
                logger.warning('[MEDIA] could not fetch audio for task %s: %s',
                               task.id, exc)
                return False
            return os.path.exists(path) and os.path.getsize(path) > 0

    @staticmethod
    def _task_for(request, pk):
        """
        A worker may hear audio for a task they HOLD, or for one still in
        the open pool.

        WHY PREVIEW IS ALLOWED. Claiming starts Eagle's one-hour clock, and
        it cannot be undone by changing your mind -- so a tasker deciding
        whether to take a task is deciding blind unless they can listen
        first. Heavy accents, poor recording and crosstalk are exactly what
        makes a task take two hours instead of one, and none of it is
        visible in a duration and a filename.

        NOTHING IS EXPOSED BY THIS. The pool is already listed to every
        worker, the bytes are served from OUR cache by OUR endpoint, and no
        Eagle URL or cookie is involved either way.

        A task somebody else holds stays invisible: 404, not 403, because
        whether it exists is itself information.
        """
        task = Task.objects.filter(pk=pk).first()
        if task is None:
            raise Http404
        if is_admin(request.user):
            return task
        profile = worker_profile(request.user)
        if profile is None:
            raise Http404
        if task.claimed_by_id == profile.id:
            return task
        # In the open pool: unclaimed, unassigned, still claimable.
        if (task.claimed_by_id is None and task.assigned_to_id is None
                and task.status in states.CLAIMABLE_STATES):
            return task
        raise Http404




#: *italic* / **bold** / _underscore_ wrapping real text. Deliberately not a
#: general markdown parser: the only thing worth removing is emphasis an
#: assistant added around titles, and anything cleverer risks eating
#: punctuation the speaker actually used.
_MD_EMPHASIS_RE = re.compile(r'(?<!\w)(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1(?!\w)',
                             re.DOTALL)


def _strip_markdown_emphasis(text: str) -> str:
    """Remove *emphasis* markers, keeping the words they wrapped."""
    previous = None
    # Twice at most: **bold italic** nests one level in practice.
    for _ in range(2):
        if text == previous:
            break
        previous = text
        text = _MD_EMPHASIS_RE.sub(r'\2', text)
    return text


class TranslationDraftView(views.APIView):
    """
    GET  /workforce/tasks/<id>/translation/ -- the French, beside the English.
    POST                                    -- the worker's polished French.

    WHY THIS EXISTS. The review screen used to show only `job.segments`,
    which on a translation task are the ENGLISH source, while the submitter
    rendered from the French revision. A worker therefore looked at one
    document, edited it, and submitted a different one -- their edits went
    nowhere and nothing said so.

    Both are returned here, aligned by index. They come from the same
    transcript, so segment N of the source is segment N of the translation;
    the pairing is not a guess.

    THE POST STORES PROSE, not segments. Eagle's translation deliverable has
    no timestamps and no speaker labels, so a whole-document paste-back has
    nothing to realign -- which is exactly why the copy-out/paste-back
    workflow is safe here and would not be on a transcription task.
    """

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAuthenticatedWorkerOrAdmin]

    def _task(self, request, pk, must_hold=False):
        task = Task.objects.filter(pk=pk).first()
        if task is None or task.kind != Task.TRANSLATION:
            raise Http404
        if is_admin(request.user):
            return task
        profile = worker_profile(request.user)
        if profile is None:
            raise Http404
        if task.claimed_by_id == profile.id:
            return task
        openly_claimable = (
            task.claimed_by_id is None and task.assigned_to_id is None
            and task.status in states.CLAIMABLE_STATES
        )
        if must_hold:
            # Reading the pool is fine; writing to a task you do not hold
            # is not -- UNLESS training mode is on, in which case the open
            # pool gets the same write access as the holder. See
            # WORKFORCE_TRAINING_MODE's own docstring in settings.py.
            if training_mode_active() and openly_claimable:
                return task
            raise Http404
        if openly_claimable:
            return task
        raise Http404

    def get(self, request, pk):
        task = self._task(request, pk)

        # `id` AND `end_time` ARE NOT DECORATION.
        #
        # end_time is what lets the screen say WHICH line the audio is on: a
        # highlight needs a window, and a list of start times alone can only
        # say "somewhere after here". Without it the follow-along on this
        # screen is silently dead -- it renders, it just never lights up.
        #
        # id is what lets a worker correct the English. On a translation task
        # the English is an input, but an input with a mis-heard word
        # produces a French sentence that is wrong about the world, and the
        # only honest repair is upstream. Editing needs something to address.
        source = []
        if task.job_id:
            source = [
                {'id': str(seg.id), 'start_time': seg.start_time,
                 'end_time': seg.end_time, 'text': seg.text,
                 'is_edited': seg.original_text is not None}
                for seg in task.job.segments.all().order_by('start_time')
            ]

        draft = []
        if task.deliverable_revision_id:
            draft = [
                {'start_time': seg.start_time, 'end_time': seg.end_time,
                 'text': seg.effective_text}
                for seg in task.deliverable_revision.segments.all().order_by('start_time')
            ]

        return Response({
            'task': str(task.id),
            'source_language': task.source_language,
            'target_language': task.target_language,
            'source': source,
            'draft': draft,
            # The body a worker copies into their assistant: the machine
            # translation as prose, which is the shape the prompt expects.
            'draft_text': '\n\n'.join(row['text'] for row in draft if row['text']),
            # THE PROMPT TRAVELS WITH THE TEXT.
            #
            # This screen told workers to paste the text "under the polish
            # prompt" and then supplied no prompt, so each of them brought
            # their own to their own assistant -- one standard per worker,
            # discovered a day later when the reviews came back.
            'prompts': worker_prompts.build(
                task.source_language, task.target_language,
                source_text='\n\n'.join(
                    row['text'] for row in source if row['text']),
            ),
            'polished_text': task.polished_text,
            'polished_by': (task.polished_by.worker_code
                            if task.polished_by_id else None),
            'polished_at': task.polished_at,
            # A REAL REPORT: reloading the page after saving showed
            # 'a polished version is already saved' and nothing else --
            # the box was empty and the VERIFY AGAINST AUDIO list was
            # gone, as though the work had vanished. It had not:
            # polished_text above is exactly that saved work, just never
            # used to fill the box back in. verify_against_audio has
            # nowhere on Task to live (see TRANSLATION_POLISHED's own
            # docstring -- it is written to the event log, not a column),
            # so the most recent save's note is read back from there
            # instead of adding a migration nobody asked for. Only the
            # LATEST polish's note matters -- an older one belongs to a
            # version that is no longer what is saved.
            'verify_against_audio': (
                task.events
                    .filter(event_type=TaskEvent.TRANSLATION_POLISHED)
                    .order_by('-id')
                    .values_list('metadata', flat=True)
                    .first() or {}
            ).get('verify_against_audio'),
        })

    def post(self, request, pk):
        task = self._task(request, pk, must_hold=True)
        text = str(request.data.get('text') or '').strip()

        if not text:
            return Response(
                {'error': 'Paste the polished translation before saving.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(text) > 200000:
            return Response(
                {'error': 'That is far longer than any task should be.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # RECOVER THE DOCUMENT FROM THE WHOLE REPLY, MARKERS AND ALL.
        #
        # worker_prompts._POLISH/_REVIEW ask an assistant to wrap the
        # document in START/END markers so there is an unambiguous
        # boundary -- but a worker's own assistant UI (seen live: Gemini's
        # canvas view) often offers one copy button for its entire reply,
        # which copies the markers right along with it. Rather than rely
        # on a worker hand-selecting the interior text, the whole paste is
        # accepted and the document is pulled out of it here -- the same
        # way a person reading it would. See worker_prompts.py's own
        # comment above strip_reply_scaffolding for the fuller story.
        text, verify_note = worker_prompts.strip_reply_scaffolding(text)
        if not text:
            return Response(
                {'error': 'Paste the polished translation before saving.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # STRIP MARKDOWN EMPHASIS.
        #
        # Assistants italicise book titles -- *The Mind of the South* --
        # because they are writing for a screen that renders markdown.
        # Eagle receives plain text, and the accepted submissions carry no
        # asterisks, so they would arrive as literal punctuation in the
        # middle of a sentence.
        #
        # Only emphasis is removed, and only where it wraps text: a lone
        # asterisk, or one used as a bullet, is left exactly as pasted.
        # Nothing else about the paste is touched -- this is the one
        # transformation, and it is reversible by reading the result.
        text = _strip_markdown_emphasis(text)

        profile = worker_profile(request.user)
        # True only when _task's training-mode branch is what let this
        # request through -- not the holder, not admin, so the ONLY way it
        # reached here at all was the open-pool allowance. Tagged on the
        # event so this stays distinguishable from a real submission's
        # audit trail (see WORKFORCE_TRAINING_MODE in settings.py).
        via_training = (
            training_mode_active() and not is_admin(request.user)
            and task.claimed_by_id != (profile.id if profile else None)
        )
        task.polished_text = text
        task.polished_by = profile
        task.polished_at = timezone.now()
        task.save(update_fields=['polished_text', 'polished_by',
                                 'polished_at', 'updated_at'])

        event_metadata = {'characters': len(text), 'words': len(text.split())}
        if via_training:
            event_metadata['training_mode'] = True
        if verify_note:
            # NEVER SILENTLY DROPPED. strip_reply_scaffolding already took
            # the footer out of the saved document -- if it named a real
            # spot to check, that has to survive somewhere a worker (and
            # a reviewer looking at the audit trail later) can still see
            # it, not vanish along with the marker text around it.
            event_metadata['verify_against_audio'] = verify_note
        record_event(
            task, TaskEvent.TRANSLATION_POLISHED, actor=request.user,
            message='Polished translation pasted back.',
            metadata=event_metadata,
        )

        # Hand back the gate's verdict immediately: the whole point of
        # storing this is that it gets checked before anyone submits it.
        preview = preview_submission(task)
        return Response({
            'saved': True,
            'words': len(text.split()),
            'verify_against_audio': verify_note or None,
            'findings': preview.findings,
            'blocking': preview.blocking,
        })

class TranscriptCheckPromptView(views.APIView):
    """
    /workforce/tasks/<id>/transcript-check-prompt/ -- the accuracy-check
    prompt for a transcription task, and the record that it was run.

    THE MISSING HALF OF worker_prompts.py. That module already closed this
    exact gap for translation -- a worker used to bring their own prompt to
    their own assistant, one standard per worker, discovered when the
    reviews came back a day later. Nothing offered the same thing on a
    transcription task, even though the biggest thing the automated checks
    cannot see at all -- a real name or technical term misheard as a
    different real word -- is exactly as real here (see
    docs/TRANSCRIPTION_QUALITY_FINDINGS_2026-09-12.md).

    GET IS REPORT-ONLY; POST NEVER REWRITES THE TRANSCRIPT EITHER. Unlike
    the translation prompt, this hands back leads to go verify against the
    audio, not a document to paste over the transcript -- see
    worker_prompts.build_transcript_check's own docstring for why a rewrite
    variant was deliberately not built. A worker copies the prompt, pastes
    the transcript under it (the same server-rendered document Preview
    already shows), reads what comes back, and acts on it by editing the
    specific line after listening -- through the editing this screen
    already offers, never through this endpoint.

    THE POST IS AN AUDIT TRAIL, NOT A DOCUMENT SWAP. What it stores
    (`Task.accuracy_check_notes`) is proof the step happened and what it
    found, for a coach reading a rejection later -- and it is what
    `run_quality_gate` now requires before a transcription task can be
    submitted at all: the audit-and-hand-patch loop that produced most of
    this module's own exemptions does not scale to every niche a new
    recording can be about, and an LLM reading the actual document does not
    need a niche-specific rule pre-written for it. Pushing that judgment
    onto the worker's own assistant, on the worker's own account, is what
    keeps this off our token budget entirely.

    STANDARD-AWARE, so the prompt never asks a worker to "fix" a
    contraction on a strict-verbatim task, which would ask them to break
    the very standard the task is graded on.
    """

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAuthenticatedWorkerOrAdmin]

    def get(self, request, pk):
        task = TaskMediaView._task_for(request, pk)
        if task.kind != Task.TRANSCRIPTION:
            raise Http404
        standard = TaskSerializer(task, context={'request': request}).data.get(
            'transcription_standard') or {}
        return Response({
            'prompt': worker_prompts.build_transcript_check(standard.get('name', '')),
            'accuracy_check_notes': task.accuracy_check_notes,
            'accuracy_check_by': (task.accuracy_check_by.worker_code
                                  if task.accuracy_check_by_id else None),
            'accuracy_check_at': task.accuracy_check_at,
        })

    def post(self, request, pk):
        # WRITING REQUIRES HOLDING THE TASK, unlike GET above. Reading the
        # prompt is allowed from the open pool (see TaskMediaView._task_for)
        # so a worker can see what the check involves before claiming;
        # recording that it was actually run is not something anyone but
        # the holder -- or an admin -- can do. UNLESS training mode is on,
        # in which case the open pool gets the same write access as the
        # holder -- see WORKFORCE_TRAINING_MODE's own docstring in
        # settings.py, and TranslationDraftView._task for the same pattern
        # on the translation side.
        task = Task.objects.filter(pk=pk).first()
        if task is None or task.kind != Task.TRANSCRIPTION:
            raise Http404
        profile = worker_profile(request.user)
        is_holder = profile is not None and task.claimed_by_id == profile.id
        openly_claimable = (
            task.claimed_by_id is None and task.assigned_to_id is None
            and task.status in states.CLAIMABLE_STATES
        )
        via_training = (
            not is_holder and not is_admin(request.user)
            and training_mode_active() and openly_claimable
        )
        if not is_admin(request.user) and not is_holder and not via_training:
            raise Http404

        notes = str(request.data.get('notes') or '').strip()
        if not notes:
            return Response(
                {'error': "Paste what your assistant found before saving."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(notes) > 20000:
            return Response(
                {'error': 'That is far longer than an accuracy-check reply '
                          'should be.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        task.accuracy_check_notes = notes
        task.accuracy_check_by = profile
        task.accuracy_check_at = timezone.now()
        task.save(update_fields=['accuracy_check_notes', 'accuracy_check_by',
                                 'accuracy_check_at', 'updated_at'])

        check_metadata = {'characters': len(notes)}
        if via_training:
            check_metadata['training_mode'] = True
        record_event(
            task, TaskEvent.ACCURACY_CHECK_COMPLETED, actor=request.user,
            message='Accuracy-check reply pasted back.',
            metadata=check_metadata,
        )

        # Hand back the gate's verdict immediately -- the whole point of
        # requiring this step is that it gets checked before anyone submits.
        preview = preview_submission(task)
        return Response({
            'saved': True,
            'findings': preview.findings,
            'blocking': preview.blocking,
        })


class SubmissionPreviewView(views.APIView):
    """
    GET /workforce/tasks/<id>/preview/ -- exactly what Eagle will receive.

    Rendered by the backend, not the browser. A worker seeing the real
    document before they send it is the cheapest check available against a
    rejection, and it costs five seconds of an hour.
    """

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAuthenticatedWorkerOrAdmin]

    def get(self, request, pk):
        task = TaskMediaView._task_for(request, pk)
        try:
            preview = preview_submission(task)
        except SubmissionRefused as exc:
            return Response({'error': exc.safe_message, 'findings': exc.findings},
                            status=status.HTTP_409_CONFLICT)
        return Response({
            'content': preview.content,
            'format': preview.format_name,
            'characters': preview.character_count,
            'words': preview.word_count,
            'findings': preview.findings,
            'blocking': preview.blocking,
        })


class SubmitToEagleView(views.APIView):
    """
    POST /workforce/tasks/<id>/submit-external/ -- send it.

    Distinct from the Phase 2 `submit` action, which only RECORDS that a
    submission happened. This one performs it: Eagle first, our state after
    acknowledgement.
    """

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAuthenticatedWorkerOrAdmin]
    # EVERY CALL HERE SPENDS SOMETHING IRREVERSIBLE: it sends work to
    # the platform. Sixty an hour is far above six people submitting
    # real tasks and far below anything that could double-submit a
    # queue by accident.
    throttle_scope = 'submit'

    def post(self, request, pk):
        task = TaskMediaView._task_for(request, pk)
        # `force` is the deadline endgame and is an ADMIN decision or an
        # automated one -- never a button a worker can press to skip the
        # gate on an ordinary submission.
        force = bool(request.data.get('force')) and is_admin(request.user)

        try:
            submitted = submit_task(
                task.id, request.user, force=force,
                reason=str(request.data.get('reason') or '')[:500],
            )
        except SubmissionRefused as exc:
            return Response({'error': exc.safe_message, 'findings': exc.findings},
                            status=status.HTTP_409_CONFLICT)
        except SubmissionFailed as exc:
            logger.error('[SUBMIT] task %s failed: %s', task.id, exc.detail)
            return Response({'error': exc.safe_message},
                            status=status.HTTP_502_BAD_GATEWAY)
        except states.IllegalTransition as exc:
            return Response({'error': exc.safe_message},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response(TaskSerializer(submitted, context={'request': request}).data)


class WorkQueueStatusView(views.APIView):
    """
    GET /workforce/work/status/ -- is there work, and can we afford it?

    Answers the two questions a worker screen asks on every poll without
    exposing the economics: how many drafts are ready, and whether the
    platform session is healthy. Minutes and payouts are ADMIN-only.
    """

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAuthenticatedWorkerOrAdmin]

    def get(self, request):
        # ready_for_work_q() is the shared definition (see its docstring):
        # job__status='completed' alone overcounts a translation whose
        # translate pass failed but whose transcript still completed.
        warm = Task.objects.filter(
            status=states.NEW, claimed_by__isnull=True, job__isnull=False,
        ).filter(ready_for_work_q()).count()
        payload = {'ready_now': warm}

        if is_admin(request.user):
            from .credentials import EagleCredential

            credentials = list(EagleCredential.objects.all())
            payload['platform'] = [
                {
                    'product': c.product,
                    'status': c.status,
                    'minutes_balance': c.minutes_balance,
                    'last_verified_at': c.last_verified_at,
                }
                for c in credentials
            ]
            # A zero balance means every claim fails with InsufficientMinutes
            # while the queue still shows those tasks as "Ready now" -- the
            # whole team pounding on work nobody can start, and nothing
            # actively telling the one person who can fix it. Only raised
            # when there IS ready work to be blocked. Admin-only, like the
            # rest of this block: the economics stay off the tasker's screen
            # (they already get a plain per-claim message). A dead session
            # (EXPIRED) is a separate banner.
            payload['minutes_exhausted'] = bool(warm) and any(
                c.status != EagleCredential.EXPIRED
                and c.minutes_balance is not None and c.minutes_balance <= 0
                for c in credentials
            )
            payload['pool_target'] = warmpool.DEFAULT_POOL_SIZE
        return Response(payload)
