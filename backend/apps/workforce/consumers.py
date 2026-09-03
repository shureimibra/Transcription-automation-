"""
Live task updates.

THE SOCKET IS A HINT, NOT A SOURCE OF TRUTH. Every message says only "task X
changed, it is now in state Y". The client re-fetches through the REST API,
which applies `visible_tasks_for` and the serializer for that caller's role.
That is deliberate: pushing task *contents* down a socket would mean the
authorization rules are enforced in two places, and the second one is always
the one that gets it wrong. Nothing here carries a transcript, a note, a
failure detail, a URL or a credential.

THIS CONSUMER AUTHENTICATES. Phase 1 recorded that the existing
TranscriptionConsumer accepts every connection without looking at
`scope['user']` -- anyone who can guess a job id can watch it. That endpoint
is unchanged here (its callers and tests are Phase 5's problem), but nothing
new is built on that footing: this one closes an unauthenticated connection
before joining any group.

GROUPS ARE THE AUTHORIZATION BOUNDARY, chosen once at connect time from the
authenticated user:

  workforce.admins      every task change (admins can already see every task)
  workforce.queue       changes to tasks entering OR LEAVING the open pool.
                        Arrivals are obvious; departures matter just as
                        much, because a claimed task that is never announced
                        stays on every other tasker's screen with a Claim
                        button on work that is already gone. The message
                        says only that the task moved -- never who took it,
                        which the re-fetch decides per caller.
  workforce.worker.<id> changes to tasks that worker holds or is assigned

A worker is therefore never told that someone else's task moved.
"""
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger('workforce')

ADMIN_GROUP = 'workforce.admins'
QUEUE_GROUP = 'workforce.queue'


def worker_group(profile_id) -> str:
    # Group names may not contain characters outside [a-zA-Z0-9._-]; a UUID
    # in hex form is already safe, but normalise rather than assume.
    return f'workforce.worker.{str(profile_id).replace("-", "")}'


class WorkforceConsumer(AsyncJsonWebsocketConsumer):
    """`ws/workforce/` -- one connection per signed-in browser tab."""

    async def connect(self):
        user = self.scope.get('user')
        if user is None or not user.is_authenticated:
            # 4401 rather than a silent accept: the client can tell the
            # difference between "rejected" and "connected but quiet", and
            # falls back to polling instead of waiting forever.
            await self.close(code=4401)
            return

        self.groups_joined = await self._groups_for(user)
        if not self.groups_joined:
            # Authenticated, but not a worker and not an admin -- e.g. a
            # suspended account. Nothing to subscribe to.
            await self.close(code=4403)
            return

        for group in self.groups_joined:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        for group in getattr(self, 'groups_joined', ()):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        """
        The socket is read-only. No state change is reachable from here --
        every mutation goes through the REST action endpoints, which hold the
        row lock and write the audit event. A `ping` is answered so a client
        can check liveness without reconnecting.
        """
        if isinstance(content, dict) and content.get('type') == 'ping':
            await self.send_json({'type': 'pong'})

    # -- fan-out handlers ---------------------------------------------- #

    async def task_changed(self, event):
        await self.send_json({
            'type': 'task.changed',
            'task': event['task'],
            'status': event['status'],
            'from_status': event.get('from_status', ''),
        })

    # -- helpers -------------------------------------------------------- #

    @database_sync_to_async
    def _groups_for(self, user):
        from .permissions import is_admin, worker_profile

        if is_admin(user):
            # Admins get the queue group too, so an admin watching the task
            # list sees pool changes without a second subscription.
            return [ADMIN_GROUP, QUEUE_GROUP]

        profile = worker_profile(user)
        if profile is None or not profile.is_active_worker:
            return []
        return [QUEUE_GROUP, worker_group(profile.id)]
