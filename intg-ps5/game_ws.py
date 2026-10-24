"""Fragment large Integration API messages containing Base64 SVG game artwork."""
from __future__ import annotations

import logging
from typing import Any

import websockets
from ucapi.api import IntegrationAPI
from websockets.exceptions import ConnectionClosedOK

_LOG = logging.getLogger(__name__)
FRAGMENT = 16 * 1024


class FragmentingIntegrationAPI(IntegrationAPI):
    async def _ws_producer(self, websocket: Any, ctx: Any) -> None:
        try:
            while True:
                message = await ctx.outgoing.get()
                if message is None:
                    break
                if isinstance(message, str) and len(message) > FRAGMENT:
                    fragments = tuple(
                        message[index:index + FRAGMENT]
                        for index in range(0, len(message), FRAGMENT)
                    )
                    await websocket.send(fragments)
                else:
                    await websocket.send(message)
        except (ConnectionClosedOK, websockets.exceptions.ConnectionClosedError):
            pass
