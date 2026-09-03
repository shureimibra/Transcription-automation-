import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'transcription_proj.settings')
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import apps.transcriptions.routing
import apps.workforce.routing

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(
            # Order matters only in that the patterns are distinct; the
            # workforce socket carries no id in its path, so it cannot
            # shadow the transcription one.
            apps.transcriptions.routing.websocket_urlpatterns
            + apps.workforce.routing.websocket_urlpatterns
        )
    ),
})
