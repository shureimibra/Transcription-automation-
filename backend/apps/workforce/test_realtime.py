"""
Tests for the live-update socket.

THE CLAIM WORTH TESTING is not that messages arrive -- it is WHO they arrive
for. A worker must never be told that another worker's task moved, and no
message may carry anything beyond an id and a status. Both are asserted
here, because a leak through the socket would bypass `visible_tasks_for`
entirely and nothing else in the suite would notice.

The broadcast is also asserted to happen ON COMMIT, not before: an
announcement of a state that then rolls back is worse than no announcement,
because every client would refetch and find the old value.
"""
from unittest import mock

from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase

from . import services, states
from .consumers import ADMIN_GROUP, QUEUE_GROUP, worker_group
from .models import Task, WorkerProfile
from .tests import make_task, make_worker


class RecordingLayer:
    """A channel layer that remembers what was sent where."""

    def __init__(self):
        self.sent = []

    async def group_send(self, group, message):
        self.sent.append((group, message))

    def groups_for(self, task_id):
        return {group for group, message in self.sent if message['task'] == str(task_id)}


class BroadcastTargetingTestCase(TestCase):
    """Which groups hear about a change."""

    def setUp(self):
        self.admin_user, _ = make_worker('boss', 'A-01', is_admin=True)
        self.ana_user, self.ana = make_worker('ana', 'W-01')
        self.bo_user, self.bo = make_worker('bo', 'W-02')

        self.layer = RecordingLayer()
        patcher = mock.patch('channels.layers.get_channel_layer',
                             return_value=self.layer)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, operation, *args, **kwargs):
        # on_commit callbacks do not fire inside a TestCase's wrapping
        # transaction unless they are captured explicitly.
        with self.captureOnCommitCallbacks(execute=True):
            return operation(*args, **kwargs)

    def test_an_open_task_is_announced_to_the_queue_and_to_admins(self):
        task = make_task(status=states.ASSIGNED, assigned_to=self.ana)

        self._run(services.unassign_task, task.id, actor=self.admin_user)

        self.assertEqual(
            self.layer.groups_for(task.id),
            {ADMIN_GROUP, QUEUE_GROUP},
        )

    def test_a_claim_tells_the_open_queue_the_task_has_gone(self):
        """
        LEAVING THE POOL IS NEWS, and it used not to be.

        The rule was "once it is someone's, the pool is not told", which
        reads as a privacy rule but is not one: the task was visible to
        every worker a moment earlier, and all the message carries is that
        it moved. What the old rule actually produced was a claimed task
        sitting on every other tasker's screen until their next poll, with
        a Claim button on work that was already gone.

        WHO claimed it is still not broadcast. The payload is task, status
        and from_status; clients re-fetch through the REST API, where
        visible_tasks_for decides what each caller may see -- so a worker
        learns the task left the pool and an admin learns whose it is.
        """
        task = make_task(status=states.NEW)

        self._run(services.claim_task, task.id, worker=self.ana, actor=self.ana_user)

        groups = self.layer.groups_for(task.id)
        self.assertIn(worker_group(self.ana.id), groups)
        self.assertIn(ADMIN_GROUP, groups)
        self.assertIn(QUEUE_GROUP, groups)

        # The message still says only that it moved -- never who has it.
        payloads = [m for _, m in self.layer.sent if m.get('task') == str(task.id)]
        self.assertTrue(payloads)
        for payload in payloads:
            self.assertEqual(
                set(payload) - {'type'}, {'task', 'status', 'from_status'})

    def test_a_task_retired_because_eagle_gave_it_away_also_leaves_the_queue(self):
        """The same silence hid a task the platform reassigned: Eagle's pool
        is shared, and a task another platform worker took must disappear
        from ours rather than wait for a poll."""
        task = make_task(status=states.NEW)

        self._run(services.retire_task, task.id)

        self.assertIn(QUEUE_GROUP, self.layer.groups_for(task.id))

    def test_another_worker_is_never_told_about_a_task_they_cannot_see(self):
        task = make_task(status=states.NEW)
        self._run(services.claim_task, task.id, worker=self.ana, actor=self.ana_user)
        self.layer.sent.clear()

        self._run(services.start_task, task.id, actor=self.ana_user)

        self.assertNotIn(worker_group(self.bo.id), self.layer.groups_for(task.id))

    def test_a_released_task_returns_to_the_queue_group(self):
        task = make_task(status=states.NEW)
        self._run(services.claim_task, task.id, worker=self.ana, actor=self.ana_user)
        self.layer.sent.clear()

        self._run(services.release_task, task.id, actor=self.ana_user)

        self.assertIn(QUEUE_GROUP, self.layer.groups_for(task.id))

    def test_the_payload_carries_nothing_but_identity_and_status(self):
        task = make_task(status=states.NEW, title='Confidential deposition',
                         notes='Internal only', source_url='https://example.test/secret')

        self._run(services.claim_task, task.id, worker=self.ana, actor=self.ana_user)

        _, message = self.layer.sent[0]
        self.assertEqual(set(message), {'type', 'task', 'status', 'from_status'})
        serialized = repr(message)
        for secret in ('Confidential', 'Internal only', 'example.test'):
            self.assertNotIn(secret, serialized)

    def test_the_announcement_waits_for_the_commit(self):
        """
        The message must not go out while the transaction is still open.

        Sending inside the atomic block would announce a state that a later
        failure rolls back, and every client would refetch to find the old
        value -- a UI that flickers into a state the database never held.
        """
        task = make_task(status=states.NEW)

        # execute=False captures the callbacks without running them: this is
        # the state of the world at the moment the service returns.
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            services.claim_task(task.id, worker=self.ana, actor=self.ana_user)
            self.assertEqual(self.layer.sent, [])

        # And the send was in fact queued, not simply skipped.
        self.assertEqual(len(callbacks), 1)
        for callback in callbacks:
            callback()
        self.assertNotEqual(self.layer.sent, [])

    def test_a_broken_channel_layer_does_not_break_the_state_change(self):
        class ExplodingLayer:
            async def group_send(self, group, message):
                raise RuntimeError('channel layer is down')

        task = make_task(status=states.NEW)
        with mock.patch('channels.layers.get_channel_layer', return_value=ExplodingLayer()):
            updated = self._run(services.claim_task, task.id,
                                worker=self.ana, actor=self.ana_user)

        # The task moved regardless. The socket is a nicety; the database is
        # the record.
        self.assertEqual(updated.status, states.CLAIMED)
        self.assertEqual(Task.objects.get(pk=task.id).status, states.CLAIMED)


class ConsumerAuthTestCase(TransactionTestCase):
    """Who may connect at all."""

    def setUp(self):
        self.ana_user, self.ana = make_worker('ana', 'W-01')
        self.admin_user, _ = make_worker('boss', 'A-01', is_admin=True)
        self.suspended_user, _ = make_worker(
            'zed', 'W-09', status=WorkerProfile.SUSPENDED,
        )

    async def _connect(self, user=None):
        from .consumers import WorkforceConsumer
        communicator = WebsocketCommunicator(WorkforceConsumer.as_asgi(), '/ws/workforce/')
        if user is None:
            from django.contrib.auth.models import AnonymousUser
            communicator.scope['user'] = AnonymousUser()
        else:
            communicator.scope['user'] = user
        connected, detail = await communicator.connect()
        return communicator, connected, detail

    async def test_an_anonymous_connection_is_refused(self):
        communicator, connected, code = await self._connect(None)
        self.assertFalse(connected)
        # A distinct code so the client stops retrying instead of hammering.
        self.assertEqual(code, 4401)
        await communicator.disconnect()

    async def test_a_suspended_worker_is_refused(self):
        communicator, connected, code = await self._connect(self.suspended_user)
        self.assertFalse(connected)
        self.assertEqual(code, 4403)
        await communicator.disconnect()

    async def test_an_active_worker_connects(self):
        communicator, connected, _ = await self._connect(self.ana_user)
        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_the_socket_accepts_no_commands(self):
        communicator, connected, _ = await self._connect(self.ana_user)
        self.assertTrue(connected)

        # Anything that is not a ping is ignored outright -- there is no
        # message that changes state.
        await communicator.send_json_to({'type': 'claim', 'task': 'anything'})
        await communicator.send_json_to({'type': 'ping'})

        reply = await communicator.receive_json_from()
        self.assertEqual(reply, {'type': 'pong'})
        await communicator.disconnect()
