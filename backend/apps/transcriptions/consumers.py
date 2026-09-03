"""
Live transcription progress for one job.

WHAT THIS CLOSES. Phase 1 recorded that this consumer read only
`scope['url_route']` and never `scope['user']` -- verified by connecting an
anonymous browser context to a real job's socket and being ACCEPTED. Anyone
who could guess or obtain a job id could watch that job's segments stream in,
including the finished transcript. That is now authenticated and authorised
against the same rule the REST API uses.

WHY THE SAME RULE, IMPORTED. `may_access_job` is the single definition of who
may reach a job. If this file re-derived it -- "owner or staff" written out
again -- the two would drift, and the socket would become the way around the
API's boundary. The point of one function is that there is one answer.
"""
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class TranscriptionConsumer(AsyncJsonWebsocketConsumer):

    async def connect(self):
        self.job_id = self.scope['url_route']['kwargs']['job_id']
        self.room_group_name = f'transcription_{self.job_id}'

        user = self.scope.get('user')
        if user is None or not user.is_authenticated:
            # 4401 rather than a silent accept, so the client can tell
            # "rejected" from "connected but quiet" and stop retrying.
            await self.close(code=4401)
            return

        if not await self._may_watch(user, self.job_id):
            # 4403 for "signed in, not yours". Deliberately the same code
            # whether the job belongs to someone else or does not exist:
            # distinguishing them would confirm which job ids are real.
            logger.warning('[TRANSCRIPTION] socket denied: user=%s job=%s',
                           user.username, self.job_id)
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        # Guard on the attribute: a connection refused above never joined a
        # group, and Channels still calls disconnect().
        if getattr(self, 'room_group_name', None):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        """
        Read-only. The socket reports progress; it starts nothing and
        changes nothing. Every mutation goes through the REST API, which
        authorises per request.
        """
        return

    async def transcription_update(self, event):
        await self.send_json(event['data'])

    @database_sync_to_async
    def _may_watch(self, user, job_id) -> bool:
        from .models import TranscriptionJob
        from .permissions import may_access_job

        job = TranscriptionJob.objects.filter(pk=job_id).first()
        if job is None:
            return False
        return may_access_job(user, job)
