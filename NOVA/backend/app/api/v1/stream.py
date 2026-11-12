"""Live event stream over WebSocket – the HUD's feed.

``WS /api/v1/games/{game_id}/stream``

Design
------
The stream is a **read model over the persisted event log**, never a second
source of truth:

1. subscribe to the in-process :class:`~app.systems.event_bus.EventBus` *before*
   reading the log, so nothing can slip between backfill and live tail;
2. replay everything after ``after_seq`` from the database (in pages);
3. push committed events as they happen – no polling loop, no per-socket query
   every few hundred milliseconds;
4. reconcile against the log on a slow timer, so a multi-worker deployment (the
   bus is process-local) still delivers every event;
5. a slow consumer overflows its buffer, gets ``stream.resync`` and is refilled
   from its cursor. A reconnect with ``after_seq`` never loses an event.

Database reads are shielded from cancellation (:mod:`app.core.cancellation`):
a disconnecting peer must not interrupt the driver mid-query.

Protocol (``nova.stream/1``)
---------------------------
Server → client::

    stream.ready    {protocol, game_id, after_seq, event_seq, player_name,
                     heartbeat_seconds, types, resumed}
    events          {events: [...], next_seq}          # next_seq = log cursor
    pong            {cursor, event_seq}
    heartbeat       {cursor, server_time}
    stream.resync   {reason, cursor}                   # buffer overflow
    stream.error    {code, message}                    # bad client message

Client → server: ``ping`` (plain text) or JSON ``{"type": "ping"}``,
``{"type": "resume", "after_seq": N}``. Anything else answers ``stream.error``
and keeps the socket open – a buggy HUD cannot kill its own feed.

Close codes: ``4400`` bad cursor · ``4401`` bad session token · ``4404`` unknown
save · ``4429`` too many sockets for one save · ``1000`` normal · ``1011`` fault.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.core.cancellation import shield_db_call
from app.core.errors import NovaError
from app.core.logging import get_logger
from app.core.security import constant_time_equals, verify_session_token
from app.core.utils import iso, utcnow
from app.services.game_service import GameService
from app.systems.event_bus import EventBus, SubscriptionOverflow

logger = get_logger("nova.api.stream")
router = APIRouter(tags=["Stream"])

PROTOCOL = "nova.stream/1"

CLOSE_BAD_CURSOR = 4400
CLOSE_AUTH_FAILED = 4401
CLOSE_NOT_FOUND = 4404
CLOSE_TOO_MANY = 4429
CLOSE_INTERNAL = 1011

#: Never wait longer than this for inbox traffic – keeps shutdown responsive.
MAX_WAIT_SECONDS = 1.0
#: Upper bound on events merged into one frame.
MAX_BATCH = 500


class _Inbox:
    """Merged queue of live events, client messages and disconnect notices."""

    def __init__(self, maxsize: int) -> None:
        self.queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self.overflowed = False

    def offer_events(self, events: list[dict[str, Any]]) -> None:
        try:
            self.queue.put_nowait(("events", events))
        except asyncio.QueueFull as exc:
            self.overflowed = True
            raise SubscriptionOverflow from exc

    def offer(self, kind: str, payload: Any = None) -> None:
        """Client frames and the disconnect notice are never dropped.

        A full buffer is made room in by evicting the *oldest* entry: losing an
        event batch is recoverable (the log refills it from the cursor), losing
        the disconnect notice would keep a dead socket alive.
        """
        while True:
            try:
                self.queue.put_nowait((kind, payload))
                return
            except asyncio.QueueFull:
                try:
                    evicted, _ = self.queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - single consumer
                    continue
                if evicted == "events":
                    self.overflowed = True

    def drain(self, first: tuple[str, Any] | None = None) -> list[tuple[str, Any]]:
        items = [first] if first is not None else []
        while len(items) < MAX_BATCH:
            try:
                items.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items


async def _send(websocket: WebSocket, payload: dict[str, Any]) -> bool:
    """Send one frame; ``False`` means the peer is gone."""
    if websocket.client_state != WebSocketState.CONNECTED:
        return False
    try:
        await websocket.send_json(payload)
    except (WebSocketDisconnect, RuntimeError):
        return False
    return True


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    with contextlib.suppress(Exception):
        await websocket.close(code=code, reason=reason)


def _authorised(websocket: WebSocket, game_id: str, token: str | None, api_key: str | None) -> bool:
    settings = websocket.app.state.settings
    if not settings.auth_enabled:
        return True
    if settings.api_key and api_key and constant_time_equals(api_key, settings.api_key):
        return True
    presented = token or websocket.headers.get("x-session-token") or ""
    try:
        verify_session_token(presented, settings.secret_key, game_id)
    except NovaError:
        return False
    return True


async def _reader(websocket: WebSocket, inbox: _Inbox) -> None:
    """Pump client frames into the inbox. Never touches the database."""
    try:
        while True:
            message = await websocket.receive()
            kind = message.get("type")
            if kind == "websocket.disconnect":
                inbox.offer("closed")
                return
            if kind != "websocket.receive":
                continue
            text = message.get("text")
            if text is None and message.get("bytes") is not None:
                text = message["bytes"].decode("utf-8", errors="replace")
            inbox.offer("client", text or "")
    except (WebSocketDisconnect, RuntimeError):
        inbox.offer("closed")


@router.websocket("/games/{game_id}/stream")
async def stream(
    websocket: WebSocket,
    game_id: str,
    after_seq: int = Query(default=0, description="Replay the log after this sequence number."),
    token: str | None = Query(
        default=None, description="X-Session-Token equivalent when auth is on."
    ),
    api_key: str | None = Query(default=None, description="Trusted server-to-server key."),
    types: str | None = Query(
        default=None, description="Comma separated event types, e.g. mission.completed,oxygen.*"
    ),
) -> None:
    games: GameService = websocket.app.state.games
    bus: EventBus = websocket.app.state.event_bus
    settings = games.settings
    loop = asyncio.get_running_loop()

    if after_seq < 0:
        await _close(websocket, CLOSE_BAD_CURSOR, "after_seq must be >= 0")
        return
    if not _authorised(websocket, game_id, token, api_key):
        await _close(websocket, CLOSE_AUTH_FAILED, "Invalid session token")
        return
    if bus.subscriber_count(game_id) >= settings.stream_max_connections_per_game:
        await _close(websocket, CLOSE_TOO_MANY, "Too many sockets for this save")
        return

    try:
        state = await shield_db_call(games.read_state(game_id))
    except NovaError:
        await _close(websocket, CLOSE_NOT_FOUND, "Game session not found")
        return

    wanted = _parse_types(types)
    cursor = after_seq
    head = state.event_seq
    inbox = _Inbox(settings.stream_queue_size)
    heartbeat_seconds = settings.stream_heartbeat_seconds
    reader: asyncio.Task[None] | None = None

    # Subscribe *before* the backfill so a commit landing mid-replay is queued
    # instead of lost. Duplicates are filtered by the cursor.
    with bus.subscription(game_id, inbox.offer_events) as subscription:
        await websocket.accept()
        logger.info(
            "stream.connected",
            extra={"game_id": game_id, "after_seq": cursor, "subscription": subscription.id},
        )

        async def ready(resumed: bool = False) -> bool:
            return await _send(
                websocket,
                {
                    "type": "stream.ready",
                    "protocol": PROTOCOL,
                    "game_id": game_id,
                    "after_seq": cursor,
                    "event_seq": head,
                    "player_name": state.player_name,
                    "heartbeat_seconds": heartbeat_seconds,
                    "types": sorted(wanted) if wanted else [],
                    "resumed": resumed,
                },
            )

        async def flush(events: list[dict[str, Any]]) -> bool:
            """Advance the cursor over everything seen, deliver what matches."""
            nonlocal cursor, head
            if not events:
                return True
            head = max(head, events[-1]["seq"])
            cursor = max(cursor, events[-1]["seq"])
            delivered = [e for e in events if _matches(e, wanted)]
            if not delivered:
                return True
            return await _send(
                websocket, {"type": "events", "events": delivered, "next_seq": cursor}
            )

        async def backfill() -> bool:
            """Replay the log from the cursor, page by page."""
            while True:
                page = await shield_db_call(
                    games.events(game_id, after_seq=cursor, limit=settings.stream_batch_size)
                )
                if not await flush(page["events"]):
                    return False
                if not page["has_more"]:
                    return True

        if not await ready() or not await backfill():
            await _close(websocket, 1000, "Client went away")
            return

        reader = asyncio.create_task(
            _reader(websocket, inbox), name=f"nova-stream-reader-{game_id}"
        )
        next_heartbeat = loop.time() + heartbeat_seconds
        next_reconcile = loop.time() + settings.stream_reconcile_seconds

        try:
            while True:
                now = loop.time()
                wait = min(MAX_WAIT_SECONDS, max(0.01, min(next_heartbeat, next_reconcile) - now))
                first: tuple[str, Any] | None = None
                with contextlib.suppress(TimeoutError):
                    first = await asyncio.wait_for(inbox.queue.get(), timeout=wait)

                pending: list[dict[str, Any]] = []
                closed = False
                for kind, payload in inbox.drain(first):
                    if kind == "events":
                        pending.extend(payload)
                        continue
                    # Deliver queued events *before* answering the client, so a
                    # ping is a reliable "everything committed is now sent"
                    # barrier for the HUD (and for the tests).
                    if pending and not await flush(_dedupe(pending, cursor)):
                        closed = True
                        break
                    pending = []
                    if kind == "closed":
                        closed = True
                        break
                    action = _parse_message(payload)
                    if action == "ping":
                        if not await _send(
                            websocket, {"type": "pong", "cursor": cursor, "event_seq": head}
                        ):
                            closed = True
                            break
                    elif isinstance(action, int):
                        cursor = action
                        subscription.acknowledge()
                        inbox.overflowed = False
                        if not await ready(resumed=True) or not await backfill():
                            closed = True
                            break
                    elif not await _send(
                        websocket,
                        {
                            "type": "stream.error",
                            "code": "unknown_message",
                            "message": 'Expected \'ping\' or {"type": "resume", ...}.',
                        },
                    ):
                        closed = True
                        break
                if closed:
                    break

                if pending and not await flush(_dedupe(pending, cursor)):
                    break

                if subscription.overflowed or inbox.overflowed:
                    subscription.acknowledge()
                    inbox.overflowed = False
                    if not await _send(
                        websocket,
                        {"type": "stream.resync", "reason": "slow_consumer", "cursor": cursor},
                    ):
                        break
                    if not await backfill():
                        break
                    next_reconcile = loop.time() + settings.stream_reconcile_seconds

                now = loop.time()
                if now >= next_reconcile:
                    next_reconcile = now + settings.stream_reconcile_seconds
                    if not await backfill():
                        break
                if now >= next_heartbeat:
                    next_heartbeat = now + heartbeat_seconds
                    if not await _send(
                        websocket,
                        {
                            "type": "heartbeat",
                            "cursor": cursor,
                            "server_time": iso(utcnow()),
                        },
                    ):
                        break
        except WebSocketDisconnect:
            pass
        except Exception:  # pragma: no cover - defensive, never leak a stack to the client
            logger.exception("stream.failed", extra={"game_id": game_id})
            await _close(websocket, CLOSE_INTERNAL, "Stream failure")
        finally:
            if reader is not None:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader
            await _close(websocket, 1000, "Stream closed")
            logger.info(
                "stream.disconnected",
                extra={"game_id": game_id, "cursor": cursor, "subscription": subscription.id},
            )


def _parse_types(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _matches(event: dict[str, Any], wanted: set[str]) -> bool:
    if not wanted:
        return True
    kind = str(event.get("type", ""))
    if kind in wanted:
        return True
    return any(pattern.endswith("*") and kind.startswith(pattern[:-1]) for pattern in wanted)


def _dedupe(events: list[dict[str, Any]], cursor: int) -> list[dict[str, Any]]:
    """Keep log order and drop anything the cursor already covered."""
    fresh = [event for event in events if int(event["seq"]) > cursor]
    fresh.sort(key=lambda event: int(event["seq"]))
    return fresh


def _parse_message(raw: str) -> str | int | None:
    """``"ping"`` · ``after_seq`` for a resume request · ``None`` for garbage."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.lower() == "ping":
        return "ping"
    try:
        message = json.loads(text)
    except ValueError:
        return None
    if not isinstance(message, dict):
        return None
    kind = message.get("type")
    if kind == "ping":
        return "ping"
    if kind == "resume":
        with contextlib.suppress(TypeError, ValueError):
            requested = int(message.get("after_seq", 0))
            if requested >= 0:
                return requested
    return None
