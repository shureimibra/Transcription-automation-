"""
The Eagle tasker's endpoints, mounted under /api/v1/workforce/tasker/.

A SEPARATE SURFACE, NOT A MODE OF THE WORKER ONE. Every view here is gated
on the EAGLE capability and works on the caller's own EagleTaskerCopy. None
of them reads or writes through the worker endpoints, so nothing an Eagle
tasker does can change what your own workers see or submit. See
eagle_tasker.py for the rules these views apply.
"""
import logging

from django.http import Http404
from rest_framework import status, views
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from . import eagle_tasker, roles, worker_prompts
from .eagle_tasker import NotFindable
from .models import EagleTaskerCopy, Task
from .permissions import IsAdmin, account_of, has_capability
from .serializers import TaskSerializer
from .views import AUTH_CLASSES
from .worker_views import TaskMediaView, _strip_markdown_emphasis

logger = logging.getLogger('workforce')


class IsEagleTasker(BasePermission):
    message = 'Not found.'

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated
                and has_capability(request.user, roles.EAGLE)):
            return False
        # A self-registered tasker must confirm their email first.
        return account_of(request.user).email_verified_at is not None


def _task_summary(task, request) -> dict:
    """
    What the tasker's screen needs to know about a task -- and nothing about
    who on our side touched it, what it pays, or its deadline.

    `transcription_standard` comes from TaskSerializer so the banner says
    exactly what your workers' banner says for the same task.
    """
    standard = TaskSerializer(task, context={'request': request}).data.get(
        'transcription_standard')
    return {
        'id': str(task.id),
        'external_task_id': task.external_task_id,
        'kind': task.kind,
        'title': (task.topic or '').strip() or task.title,
        'project': task.project,
        'transcription_standard': standard,
        'duration_seconds': task.duration_seconds,
        'source_language': task.source_language,
        'target_language': task.target_language,
        # Still in Eagle's pool, or already claimed by somebody.
        'left_pool_at': task.cancelled_at if task.status != 'new' else None,
        'readiness': eagle_tasker.readiness(task),
        # The workspaces key their transcript load on this. It is the copy,
        # never the shared job -- the value only has to be truthy and stable.
        'job': 'copy',
        'status': 'desk',
    }


class _TaskerView(views.APIView):
    authentication_classes = AUTH_CLASSES
    permission_classes = [IsEagleTasker]

    def task_and_copy(self, request, pk):
        try:
            task = eagle_tasker.task_for(pk, request.user)
        except NotFindable:
            raise Http404
        return task, eagle_tasker.copy_for(task, request.user)


class TaskLookupView(_TaskerView):
    """POST /tasker/lookup/ {reference} -- find a task by its exact Eagle ID."""

    throttle_scope = 'tasker_lookup'

    def post(self, request):
        reference = str(request.data.get('reference') or '')[:500]
        try:
            task = eagle_tasker.find_task(reference, request.user)
        except NotFindable as exc:
            logger.info('[TASKER] %s looked up %r: not found',
                        request.user.username, reference[:80])
            return Response({'error': exc.safe_message}, status=status.HTTP_404_NOT_FOUND)

        logger.info('[TASKER] %s opened task %s (%s)', request.user.username,
                    task.external_task_id, task.kind)
        if eagle_tasker.readiness(task) == eagle_tasker.NOT_STARTED:
            eagle_tasker.start_transcription(task)
        eagle_tasker.copy_for(task, request.user)
        return Response(_task_summary(task, request))


class TaskDetailView(_TaskerView):
    """GET /tasker/tasks/<id>/ -- the same summary, for a reload or a poll."""

    def get(self, request, pk):
        task, _ = self.task_and_copy(request, pk)
        return Response(_task_summary(task, request))


class TranscriptView(_TaskerView):
    """GET /tasker/tasks/<id>/transcript/ -- the copy, shaped like a job."""

    def get(self, request, pk):
        task, copy = self.task_and_copy(request, pk)
        return Response(eagle_tasker.transcript_payload(task, copy))


class SegmentEditView(_TaskerView):
    """PATCH /tasker/tasks/<id>/segments/<seg>/ -- text and/or speaker."""

    EDITABLE_FIELDS = ('text', 'speaker_label')

    def patch(self, request, pk, segment_id):
        _, copy = self.task_and_copy(request, pk)
        rejected = [k for k in request.data if k not in self.EDITABLE_FIELDS]
        if rejected:
            return Response(
                {'error': f'These fields cannot be edited: {", ".join(sorted(rejected))}.'},
                status=status.HTTP_400_BAD_REQUEST)
        changes = {k: request.data[k] for k in self.EDITABLE_FIELDS if k in request.data}
        if not changes:
            return Response({'error': 'Provide text and/or speaker_label.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            return Response(eagle_tasker.edit_segment(copy, segment_id, changes))
        except NotFindable:
            raise Http404
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class SegmentRevertView(_TaskerView):
    def post(self, request, pk, segment_id):
        _, copy = self.task_and_copy(request, pk)
        try:
            return Response(eagle_tasker.revert_segment(copy, segment_id))
        except NotFindable:
            raise Http404
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class SegmentSplitView(_TaskerView):
    def post(self, request, pk, segment_id):
        _, copy = self.task_and_copy(request, pk)
        try:
            result = eagle_tasker.split_segment(
                copy, segment_id, request.data.get('split_at'),
                request.data.get('second_speaker'))
        except NotFindable:
            raise Http404
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_201_CREATED)


class SpeakerAliasesView(_TaskerView):
    def post(self, request, pk):
        task, copy = self.task_and_copy(request, pk)
        aliases = request.data.get('aliases')
        if not isinstance(aliases, dict):
            return Response(
                {'error': 'aliases must be an object mapping speaker labels to names.'},
                status=status.HTTP_400_BAD_REQUEST)
        try:
            eagle_tasker.set_speaker_aliases(copy, aliases)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(eagle_tasker.transcript_payload(task, copy))


class AccuracyCheckView(_TaskerView):
    """
    GET  /tasker/tasks/<id>/check/ -- the accuracy-check prompt, and what was saved.
    POST                          -- what the tasker's own assistant found.
    """

    def get(self, request, pk):
        task, copy = self.task_and_copy(request, pk)
        if task.kind != Task.TRANSCRIPTION:
            raise Http404
        standard = _task_summary(task, request).get('transcription_standard') or {}
        return Response({
            'prompt': worker_prompts.build_transcript_check(standard.get('name', '')),
            'accuracy_check_notes': copy.accuracy_check_notes,
            'accuracy_check_by': 'you' if copy.accuracy_check_at else None,
            'accuracy_check_at': copy.accuracy_check_at,
        })

    def post(self, request, pk):
        from django.utils import timezone

        task, copy = self.task_and_copy(request, pk)
        if task.kind != Task.TRANSCRIPTION:
            raise Http404
        notes = str(request.data.get('notes') or '').strip()
        if not notes:
            return Response({'error': 'Paste what your assistant found before saving.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(notes) > 20000:
            return Response({'error': 'That is far longer than a check reply should be.'},
                            status=status.HTTP_400_BAD_REQUEST)
        copy.accuracy_check_notes = notes
        copy.accuracy_check_at = timezone.now()
        copy.save(update_fields=['accuracy_check_notes', 'accuracy_check_at', 'updated_at'])
        draft = eagle_tasker.final_draft(task, copy)
        return Response({'saved': True, 'findings': draft.findings, 'blocking': draft.blocking})


class AppliedFindingView(_TaskerView):
    """
    GET  /tasker/tasks/<id>/applied-findings/ -- shaped like task events, so
         the checklist's "already applied" marks work unchanged.
    POST {phrase, suggestion}
    """

    def get(self, request, pk):
        _, copy = self.task_and_copy(request, pk)
        return Response([
            {'event_type': 'ACCURACY_CHECK_FINDING_APPLIED',
             'metadata': {'phrase': item['phrase'], 'suggestion': item['suggestion']},
             'occurred_at': item['occurred_at']}
            for item in reversed(copy.applied_findings)
        ])

    def post(self, request, pk):
        _, copy = self.task_and_copy(request, pk)
        phrase = str(request.data.get('phrase') or '').strip()
        if not phrase:
            return Response({'error': 'phrase is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(copy.applied_findings) >= 1000:
            return Response({'error': 'Too many applied findings on one task.'},
                            status=status.HTTP_400_BAD_REQUEST)
        eagle_tasker.record_applied_finding(copy, phrase, request.data.get('suggestion') or '')
        return Response({'saved': True}, status=status.HTTP_201_CREATED)


class TranslationView(_TaskerView):
    """
    GET  /tasker/tasks/<id>/translation/ -- the tasker's English beside the French.
    POST                                -- their polished French, pasted back.

    Same shape as TranslationDraftView, so the translation screen runs
    unchanged. The English is the COPY's; the machine French is the shared
    deliverable revision, read only.
    """

    def get(self, request, pk):
        task, copy = self.task_and_copy(request, pk)
        if task.kind != Task.TRANSLATION:
            raise Http404
        source = [
            {'id': row['id'], 'start_time': row['start_time'], 'end_time': row['end_time'],
             'text': row.get('text', ''), 'is_edited': row.get('original_text') is not None}
            for row in sorted(copy.segments, key=lambda r: r['start_time'])
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
            'draft_text': '\n\n'.join(row['text'] for row in draft if row['text']),
            'prompts': worker_prompts.build(
                task.source_language, task.target_language,
                source_text='\n\n'.join(row['text'] for row in source if row['text']),
            ),
            'polished_text': copy.polished_text,
            'polished_by': 'you' if copy.polished_at else None,
            'polished_at': copy.polished_at,
            'verify_against_audio': copy.verify_against_audio or None,
        })

    def post(self, request, pk):
        from django.utils import timezone

        task, copy = self.task_and_copy(request, pk)
        if task.kind != Task.TRANSLATION:
            raise Http404
        text = str(request.data.get('text') or '').strip()
        if not text:
            return Response({'error': 'Paste the polished translation before saving.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(text) > 200000:
            return Response({'error': 'That is far longer than any task should be.'},
                            status=status.HTTP_400_BAD_REQUEST)
        text, verify_note = worker_prompts.strip_reply_scaffolding(text)
        if not text:
            return Response({'error': 'Paste the polished translation before saving.'},
                            status=status.HTTP_400_BAD_REQUEST)
        copy.polished_text = _strip_markdown_emphasis(text)
        copy.polished_at = timezone.now()
        copy.verify_against_audio = verify_note or ''
        copy.save(update_fields=['polished_text', 'polished_at',
                                 'verify_against_audio', 'updated_at'])
        draft = eagle_tasker.final_draft(task, copy)
        return Response({
            'saved': True,
            'words': len(copy.polished_text.split()),
            'verify_against_audio': verify_note or None,
            'findings': draft.findings,
            'blocking': draft.blocking,
        })


class RetranslateView(_TaskerView):
    """
    POST /tasker/tasks/<id>/retranslate/ {index} -- one line, one request.

    Answers in the shape of a finished AI revision so the translation
    screen's existing re-translate handling reads it as-is.
    """

    throttle_scope = 'ai'

    def post(self, request, pk):
        from core.services.ai_assist import AIAssistError

        task, copy = self.task_and_copy(request, pk)
        if task.kind != Task.TRANSLATION:
            raise Http404
        try:
            index = int(request.data.get('index'))
        except (TypeError, ValueError):
            return Response({'error': 'index is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            text = eagle_tasker.retranslate_line(task, copy, index, request.user)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except AIAssistError as exc:
            return Response({'error': getattr(exc, 'safe_message', '') or
                             'Re-translate failed. Try again in a moment.'},
                            status=status.HTTP_429_TOO_MANY_REQUESTS)
        logger.info('[TASKER] %s re-translated line %s of %s', request.user.username,
                    index, task.external_task_id)
        return Response({'id': None, 'status': 'completed',
                         'segments': [{'effective_text': text}]})


class FinalDraftView(_TaskerView):
    """
    GET /tasker/tasks/<id>/final-draft/ -- the document to paste into Eagle.

    Same response shape as SubmissionPreviewView. `blocking` is advice here:
    nothing is submitted from this side, so the tasker decides.
    """

    def get(self, request, pk):
        from .eagle_submit import SubmissionRefused

        task, copy = self.task_and_copy(request, pk)
        try:
            draft = eagle_tasker.final_draft(task, copy)
        except SubmissionRefused as exc:
            return Response({'error': exc.safe_message, 'findings': exc.findings},
                            status=status.HTTP_409_CONFLICT)
        return Response({
            'content': draft.content,
            'format': draft.format_name,
            'characters': draft.character_count,
            'words': draft.word_count,
            'findings': draft.findings,
            'blocking': draft.blocking,
            'placeholder': eagle_tasker.NAME_PLACEHOLDER,
        })


class TaskerMediaView(TaskMediaView):
    """GET /tasker/tasks/<id>/media/ -- the same audio streaming, tasker rules."""

    permission_classes = [IsEagleTasker]

    @staticmethod
    def _task_for(request, pk):
        try:
            return eagle_tasker.task_for(pk, request.user)
        except NotFindable:
            raise Http404


class TaskerActivityView(views.APIView):
    """GET /tasker/activity/ -- admin only: who opened which task, and when."""

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAdmin]

    def get(self, request):
        copies = EagleTaskerCopy.objects.select_related('task', 'owner')[:200]
        return Response([
            {'username': c.owner.username, 'task': c.task.external_task_id,
             'kind': c.task.kind, 'opened_at': c.opened_at, 'updated_at': c.updated_at,
             'edited_lines': sum(1 for r in c.segments if r.get('original_text') is not None),
             'polished': bool(c.polished_at), 'checked': bool(c.accuracy_check_at),
             'retranslations': c.retranslate_count}
            for c in copies
        ])
