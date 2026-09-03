from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/transcription/(?P<job_id>[a-f0-9\-]+)/$', consumers.TranscriptionConsumer.as_asgi()),
]
