import json
from channels.generic.websocket import AsyncJsonWebsocketConsumer

class TranscriptionConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.job_id = self.scope['url_route']['kwargs']['job_id']
        self.room_group_name = f'transcription_{self.job_id}'

        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()
        print(f"WebSocket client connected to group: {self.room_group_name}")

    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
        print(f"WebSocket client disconnected from group: {self.room_group_name}")

    async def receive_json(self, content):
        # Simple echo/receive handlers if clients send instructions (optional)
        pass

    # Receive message from room group
    async def transcription_update(self, event):
        # Send message to WebSocket
        await self.send_json(event['data'])
