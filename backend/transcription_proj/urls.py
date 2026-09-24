from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.transcriptions.views import (
    ProjectViewSet, TranscriptionJobViewSet, ExportTranscriptView, ExportSeriesView,
    SupportedLanguagesView, SupportedTranslationLanguagesView, DiscoverSeriesView,
    DiscoverySeriesStatusView, EditSegmentView, RevertSegmentView, SplitSegmentView,
    AICapabilitiesView, AIRevisionDetailView, AIRevisionReviewView,
    AIRevisionSegmentReviewView, AIRevisionQualityReportsView,
)

router = DefaultRouter()
router.register(r'projects', ProjectViewSet, basename='project')
router.register(r'transcriptions', TranscriptionJobViewSet, basename='transcription')

urlpatterns = [
    # Django's built-in admin is deliberately NOT routed. Nothing in the
    # product depends on it -- worker/account management is the custom admin
    # UI plus the `create_worker` / `eagle_credential` management commands,
    # and raw DB access is `manage.py shell` / `dbshell` on the box. Left
    # exposed it is a brute-force target and a Django-admin-CVE surface that
    # also hands out API tokens through a web form. `django.contrib.admin`
    # stays in INSTALLED_APPS (its migration is applied; removing it there
    # is a separate, riskier change) -- without this route there is no way
    # in regardless.
    path('api/v1/', include(router.urls)),

    # Workforce & task management (Phase 2). Separate namespace from the
    # legacy transcription API above: everything under this prefix requires
    # authentication, while those endpoints do not yet.
    path('api/v1/workforce/', include('apps.workforce.urls')),
    path('api/v1/transcriptions/<uuid:job_id>/export/<str:format_type>/', ExportTranscriptView.as_view(), name='export-transcript'),
    path('api/v1/transcriptions/<uuid:job_id>/segments/<uuid:segment_id>/', EditSegmentView.as_view(), name='edit-segment'),
    path('api/v1/transcriptions/<uuid:job_id>/segments/<uuid:segment_id>/revert/', RevertSegmentView.as_view(), name='revert-segment'),
    path('api/v1/transcriptions/<uuid:job_id>/segments/<uuid:segment_id>/split/', SplitSegmentView.as_view(), name='split-segment'),
    path('api/v1/series/<uuid:series_id>/export/<str:format_type>/', ExportSeriesView.as_view(), name='export-series'),
    path('api/v1/languages/', SupportedLanguagesView.as_view(), name='supported-languages'),
    path('api/v1/translation-languages/', SupportedTranslationLanguagesView.as_view(), name='supported-translation-languages'),
    path('api/v1/discover-series/', DiscoverSeriesView.as_view(), name='discover-series'),
    path('api/v1/discover-series/<uuid:task_id>/', DiscoverySeriesStatusView.as_view(), name='discover-series-status'),

    # AI Assist. Revisions are addressed by their own id rather than nested
    # under the job, because the review UI already holds the revision id and
    # a revision belongs to exactly one job -- the same reason EditSegmentView
    # above still scopes by job_id where it needs the ownership check, and
    # these don't.
    path('api/v1/ai-capabilities/', AICapabilitiesView.as_view(), name='ai-capabilities'),
    path('api/v1/ai-revisions/<uuid:revision_id>/', AIRevisionDetailView.as_view(), name='ai-revision-detail'),
    path('api/v1/ai-revisions/<uuid:revision_id>/review/', AIRevisionReviewView.as_view(), name='ai-revision-review'),
    path('api/v1/ai-revisions/<uuid:revision_id>/quality/', AIRevisionQualityReportsView.as_view(), name='ai-revision-quality'),
    path('api/v1/ai-revisions/<uuid:revision_id>/segments/<uuid:segment_id>/', AIRevisionSegmentReviewView.as_view(), name='ai-revision-segment-review'),
]
