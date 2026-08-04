import json
import time
from collections import defaultdict

try:
    from ..app_state import state
except ImportError:
    from app_state import state


class RealtimeSyncService:
    def __init__(self):
        self._channels = defaultdict(set)

    def subscribe(self, channel_key: str, websocket):
        self._channels[channel_key].add(websocket)

    def unsubscribe(self, channel_key: str, websocket):
        subscribers = self._channels.get(channel_key)
        if not subscribers:
            return

        subscribers.discard(websocket)
        if not subscribers:
            self._channels.pop(channel_key, None)

    async def broadcast(self, channel_key: str, payload: dict):
        subscribers = list(self._channels.get(channel_key, set()))
        if not subscribers:
            return 0

        message = json.dumps(payload, ensure_ascii=True)
        delivered = 0

        for websocket in subscribers:
            try:
                await websocket.send_text(message)
                delivered += 1
            except Exception:
                self.unsubscribe(channel_key, websocket)

        state.workspace.last_broadcast_at = time.time()
        return delivered


realtime_sync = RealtimeSyncService()
