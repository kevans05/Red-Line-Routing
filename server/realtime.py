"""WebSocket hub: broadcasts newly-accepted events to connected clients.

This is purely a low-latency notification layer on top of the event log —
it is not a separate source of truth. A client that misses a broadcast
(briefly disconnected, or was offline entirely) catches up via the normal
`/sync/pull` cursor, exactly as if it had been offline for a week. See the
architecture notes' "Real-time push when online, same event log when
offline" section.
"""

import json
from collections import defaultdict


class RealtimeHub:
    def __init__(self):
        # project_id -> set of connected websockets
        self._connections = defaultdict(set)

    async def connect(self, project_id, websocket):
        await websocket.accept()
        self._connections[project_id].add(websocket)

    def disconnect(self, project_id, websocket):
        self._connections[project_id].discard(websocket)
        if not self._connections[project_id]:
            self._connections.pop(project_id, None)

    async def broadcast_event(self, project_id, event: dict, exclude=None):
        """Send an accepted event to every other client connected to this
        project. `exclude` is typically the websocket of the client whose
        own push caused this event, if it's also listening on the same
        socket (it already knows).
        """
        sockets = self._connections.get(project_id)
        if not sockets:
            return
        message = json.dumps({"type": "event", "event": event})
        dead = []
        for ws in sockets:
            if ws is exclude:
                continue
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(project_id, ws)

    def connection_count(self, project_id):
        return len(self._connections.get(project_id, ()))
