from django.urls import re_path

from . import consumers

# One socket per signed-in browser, not one per task. Which tasks a
# connection hears about is decided by the groups it joins at connect time
# from the authenticated user -- there is no id in the URL to tamper with.
websocket_urlpatterns = [
    re_path(r'ws/workforce/$', consumers.WorkforceConsumer.as_asgi()),
]
