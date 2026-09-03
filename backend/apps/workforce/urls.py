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

from .analytics import WorkforcePerformanceView
from .worker_views import (
    StartNextTaskView, SubmissionPreviewView,
    TranslationDraftView, SubmitToEagleView, TaskMediaView,
    WorkQueueStatusView,
)
from .views import (
    AccountViewSet, CurrentUserView, LoginView, LogoutView, TaskViewSet,
    WorkerViewSet, WorkforceStatsView,
)

router = DefaultRouter()
router.register(r'tasks', TaskViewSet, basename='wf-task')
router.register(r'workers', WorkerViewSet, basename='wf-worker')
router.register(r'accounts', AccountViewSet, basename='wf-account')

urlpatterns = [
    path('auth/login/', LoginView.as_view(), name='wf-login'),
    path('auth/logout/', LogoutView.as_view(), name='wf-logout'),
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
    path('tasks/<uuid:pk>/preview/', SubmissionPreviewView.as_view(), name='wf-task-preview'),
    path('tasks/<uuid:pk>/submit-external/', SubmitToEagleView.as_view(),
         name='wf-task-submit-external'),

    path('', include(router.urls)),
]
