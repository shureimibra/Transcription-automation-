from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.transcriptions.views import ProjectViewSet, TranscriptionJobViewSet, ExportTranscriptView
from apps.transcriptions.views import OotranscriptView, LanguageRegistryView, LinguisticValidationView

router = DefaultRouter()
router.register(r'projects', ProjectViewSet, basename='project')
router.register(r'transcriptions', TranscriptionJobViewSet, basename='transcription')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include(router.urls)),
    path('api/v1/transcriptions/<uuid:job_id>/export/<str:format_type>/', ExportTranscriptView.as_view(), name='export-transcript'),
    path('api/v1/ootranscript/', OotranscriptView.as_view(), name='ootranscript'),
    path('api/v1/languages/', LanguageRegistryView.as_view(), name='language-registry'),
    path('api/v1/validate-transcription/', LinguisticValidationView.as_view(), name='validate-transcription'),
]
