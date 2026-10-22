"""
Workforce routes, mounted under /api/v1/workforce/.

A separate prefix from the legacy transcription API on purpose: those
endpoints run under AllowAny (Phase 1, §H) and everything here requires
authentication. Keeping the two namespaces distinct means a reverse proxy
or a future middleware can treat them differently without pattern-matching
individual paths.
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import eagle_tasker_views as tasker
from . import tasker_auth
from .analytics import WorkforcePerformanceView
from .worker_views import (
    StartNextTaskView, SubmissionPreviewView,
    TranscriptCheckPromptView, TranslationDraftView, SubmitToEagleView,
    TaskMediaView, WorkQueueStatusView,
)
from .views import (
    AccountViewSet, CurrentUserView, DeskLoginView, LoginView, LogoutView, TaskViewSet,
    WorkerViewSet, WorkforceStatsView,
)

router = DefaultRouter()
# No index listing tasks/workers/accounts to whoever asks for /workforce/.
router.include_root_view = False
router.register(r'tasks', TaskViewSet, basename='wf-task')
router.register(r'workers', WorkerViewSet, basename='wf-worker')
router.register(r'accounts', AccountViewSet, basename='wf-account')

urlpatterns = [
    path('auth/login/', LoginView.as_view(), name='wf-login'),
    path('auth/desk-login/', DeskLoginView.as_view(), name='wf-desk-login'),
    path('auth/logout/', LogoutView.as_view(), name='wf-logout'),
    # Public: outside taskers sign themselves up (see tasker_auth.py).
    path('auth/options/', tasker_auth.AuthOptionsView.as_view(), name='wf-auth-options'),
    path('auth/signup/', tasker_auth.SignupView.as_view(), name='wf-signup'),
    path('auth/google/', tasker_auth.GoogleSignInView.as_view(), name='wf-google'),
    path('auth/verify-email/', tasker_auth.VerifyEmailView.as_view(), name='wf-verify-email'),
    path('auth/resend-code/', tasker_auth.ResendCodeView.as_view(), name='wf-resend-code'),
    path('me/', CurrentUserView.as_view(), name='wf-me'),
    path('stats/', WorkforceStatsView.as_view(), name='wf-stats'),
    # Date-filtered history. Declared before the router so the literal path
    # is matched by its own view rather than by anything the router adds.
    path('stats/performance/', WorkforcePerformanceView.as_view(), name='wf-performance'),
    # The tasker's loop: take work, hear it, see what will be sent, send it.
    # Declared before the router so these literal paths are matched by their
    # own views rather than by anything the router generates.
    path('work/next/', StartNextTaskView.as_view(), name='wf-work-next'),
    path('work/status/', WorkQueueStatusView.as_view(), name='wf-work-status'),
    path('tasks/<uuid:pk>/media/', TaskMediaView.as_view(), name='wf-task-media'),
    path('tasks/<uuid:pk>/translation/', TranslationDraftView.as_view(), name='wf-task-translation'),
    path('tasks/<uuid:pk>/transcript-check-prompt/', TranscriptCheckPromptView.as_view(),
         name='wf-task-transcript-check-prompt'),
    path('tasks/<uuid:pk>/preview/', SubmissionPreviewView.as_view(), name='wf-task-preview'),
    path('tasks/<uuid:pk>/submit-external/', SubmitToEagleView.as_view(),
         name='wf-task-submit-external'),

    # Outside Eagle taskers: exact-ID lookup and a private copy per task.
    # Under their own prefix so none of it can be mistaken for, or reach
    # through, the worker endpoints above. See eagle_tasker.py.
    path('tasker/lookup/', tasker.TaskLookupView.as_view(), name='wf-tasker-lookup'),
    path('tasker/activity/', tasker.TaskerActivityView.as_view(), name='wf-tasker-activity'),
    path('tasker/tasks/<uuid:pk>/', tasker.TaskDetailView.as_view(), name='wf-tasker-task'),
    path('tasker/tasks/<uuid:pk>/transcript/', tasker.TranscriptView.as_view(),
         name='wf-tasker-transcript'),
    path('tasker/tasks/<uuid:pk>/segments/<str:segment_id>/', tasker.SegmentEditView.as_view(),
         name='wf-tasker-segment'),
    path('tasker/tasks/<uuid:pk>/segments/<str:segment_id>/revert/',
         tasker.SegmentRevertView.as_view(), name='wf-tasker-segment-revert'),
    path('tasker/tasks/<uuid:pk>/segments/<str:segment_id>/split/',
         tasker.SegmentSplitView.as_view(), name='wf-tasker-segment-split'),
    path('tasker/tasks/<uuid:pk>/speaker-aliases/', tasker.SpeakerAliasesView.as_view(),
         name='wf-tasker-speaker-aliases'),
    path('tasker/tasks/<uuid:pk>/check/', tasker.AccuracyCheckView.as_view(),
         name='wf-tasker-check'),
    path('tasker/tasks/<uuid:pk>/applied-findings/', tasker.AppliedFindingView.as_view(),
         name='wf-tasker-applied-findings'),
    path('tasker/tasks/<uuid:pk>/translation/', tasker.TranslationView.as_view(),
         name='wf-tasker-translation'),
    path('tasker/tasks/<uuid:pk>/retranslate/', tasker.RetranslateView.as_view(),
         name='wf-tasker-retranslate'),
    path('tasker/tasks/<uuid:pk>/final-draft/', tasker.FinalDraftView.as_view(),
         name='wf-tasker-final-draft'),
    path('tasker/tasks/<uuid:pk>/media/', tasker.TaskerMediaView.as_view(),
         name='wf-tasker-media'),

    path('', include(router.urls)),
]
