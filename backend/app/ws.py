from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from fastapi import WebSocket


class WSManager:
    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        data = json.dumps(payload, separators=(",", ":"))
        dead = []
        for ws in list(self.active):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = WSManager()


async def push_loop(interval: float, build_payload: Callable[[], dict]) -> None:
    while True:
        if manager.active:
            try:
                await manager.broadcast(build_payload())
            except Exception:
                pass
        await asyncio.sleep(interval)
