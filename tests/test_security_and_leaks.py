"""Security, memory-leak, and concurrency regression tests for QuizRooms."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.quizrooms.avatars import MAX_AVATAR_UPLOAD_BYTES
from custom_components.quizrooms.const import MAX_PLAYERS_PER_ROOM, PHASE_QUESTION
from custom_components.quizrooms.coordinator import QuizRoomsCoordinator, WSClient
from custom_components.quizrooms.engine import EngineError
from custom_components.quizrooms.network_guard import (
    NetworkGuard,
    create_network_guard_middleware,
)
from custom_components.quizrooms.views import (
    QuizRoomsPublicAvatarUploadView,
    QuizRoomsWebSocketView,
)
from tests.conftest import FakeRequest

BUILTIN_PACKS_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "quizrooms" / "packs"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "quizrooms" / "frontend"


def test_pinned_media_pruned_on_room_close_and_shutdown(tmp_path: Path) -> None:
    coord = QuizRoomsCoordinator(
        None,
        builtin_packs_dir=BUILTIN_PACKS_DIR,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=FRONTEND_DIR,
    )

    async def run() -> None:
        room, _sess, host_tok = coord.engine.create_room(
            host_session_token="host_pin",
            host_nickname="PinHost",
            pack_ids=["countries_flags"],
            round_count=5,
        )
        await coord.async_start_game(room.code, host_token=host_tok)
        assert len(room.pinned_media) == 5
        assert len(coord.pack_registry._pinned_media) == 5

        # Closing the room must immediately evict unreferenced pinned media bytes
        await coord.async_close_room(room.code, host_token=host_tok)
        assert len(coord.pack_registry._pinned_media) == 0

    asyncio.run(run())


def test_bounded_avatar_upload_rejects_oversized_non_multipart_body(tmp_path: Path) -> None:
    coord = QuizRoomsCoordinator(
        None,
        builtin_packs_dir=BUILTIN_PACKS_DIR,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=FRONTEND_DIR,
    )
    view = QuizRoomsPublicAvatarUploadView(coord)

    class _StreamingOversizedRequest(FakeRequest):
        content_type = "application/octet-stream"
        content_length = MAX_AVATAR_UPLOAD_BYTES + 1024

        async def read(self) -> bytes:  # pragma: no cover - must never buffer oversized body
            raise AssertionError("Should reject via Content-Length before buffering body")

    async def run() -> None:
        resp = await view.post(
            _StreamingOversizedRequest(path="/quizrooms/api/public/avatar", remote="192.168.1.40")
        )
        assert resp.status == 413

    asyncio.run(run())


def test_network_guard_middleware_guards_avatar_and_allows_remote_authenticated_ha_admin() -> None:
    guard = NetworkGuard(allow_external_access=False)
    middleware = create_network_guard_middleware(guard)

    async def ok_handler(_req: Any) -> Any:
        from aiohttp import web

        return web.Response(status=200, text="ok")

    async def run() -> None:
        # Unauthenticated public request to /quizrooms/avatar/... must be blocked by middleware
        blocked = await middleware(
            FakeRequest(path="/quizrooms/avatar/preset/fox", remote="203.0.113.5"), ok_handler
        )
        assert blocked.status == 403

        # Authenticated remote HA Admin on /quizrooms/media/... and /quizrooms/avatar/... is allowed
        admin = SimpleNamespace(is_admin=True)
        ok_media = await middleware(
            FakeRequest(path="/quizrooms/media/abcdef1234567890abcdef12", remote="203.0.113.5", hass_user=admin),
            ok_handler,
        )
        assert ok_media.status == 200
        ok_avatar = await middleware(
            FakeRequest(path="/quizrooms/avatar/preset/fox", remote="203.0.113.5", hass_user=admin),
            ok_handler,
        )
        assert ok_avatar.status == 200

        # Authenticated remote HA user still blocked on /quizrooms/play when allow_external_access=False
        blocked_play = await middleware(
            FakeRequest(path="/quizrooms/play", remote="203.0.113.5", hass_user=admin), ok_handler
        )
        assert blocked_play.status == 403

    asyncio.run(run())


def test_max_players_per_room_cap_and_ws_session_binding_prevents_impersonation(tmp_path: Path) -> None:
    coord = QuizRoomsCoordinator(
        None,
        builtin_packs_dir=BUILTIN_PACKS_DIR,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=FRONTEND_DIR,
    )
    room, host_sess, host_tok = coord.engine.create_room(
        host_session_token="host_1",
        host_nickname="Host",
        pack_ids=["general_knowledge_en"],
        round_count=5,
    )
    # Fill room up to MAX_PLAYERS_PER_ROOM
    for i in range(1, MAX_PLAYERS_PER_ROOM):
        coord.engine.join_room(room.code, session_token=f"guest_{i}", nickname=f"Guest {i}")
    assert len(room.players) == MAX_PLAYERS_PER_ROOM

    with pytest.raises(EngineError) as exc:
        coord.engine.join_room(room.code, session_token="overflow_guest", nickname="TooMany")
    assert exc.value.code == "room_full"

    # Existing player reconnecting still succeeds even at capacity
    _, reconnected, _ = coord.engine.join_room(room.code, session_token="guest_1", nickname="Guest 1")
    assert reconnected.connected is True

    # WS socket bound to guest_1 cannot impersonate host_1 by passing host_1 in payload
    class _FakeWS:
        def __init__(self) -> None:
            self.sent: list[dict[str, Any]] = []

        async def send_json(self, payload: dict[str, Any]) -> None:
            self.sent.append(payload)

    async def run() -> None:
        await coord.async_start_game(room.code, host_token=host_tok)
        q = room.questions[0]
        ws_view = QuizRoomsWebSocketView(coord)
        attacker_client = WSClient(ws=_FakeWS(), role="room", room_code=room.code, session_token="guest_1")
        await ws_view._handle_message(
            attacker_client,
            json.dumps(
                {
                    "type": "submit_answer",
                    "code": room.code,
                    "session_token": host_sess,  # Spoofed victim token!
                    "answer": q.choices[0],
                    "wager": 1,
                }
            ),
        )
        # Submission MUST be recorded under guest_1, NEVER host_1
        assert "guest_1" in room.current_submissions
        assert host_sess not in room.current_submissions
        await coord.async_shutdown()

    asyncio.run(run())


def test_tracked_auto_reveal_is_cancelled_when_host_pauses(tmp_path: Path) -> None:
    coord = QuizRoomsCoordinator(
        None,
        builtin_packs_dir=BUILTIN_PACKS_DIR,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=FRONTEND_DIR,
    )

    async def run() -> None:
        room, host_sess, host_tok = coord.engine.create_room(
            host_session_token="host_ar",
            host_nickname="HostAR",
            pack_ids=["general_knowledge_en"],
            round_count=5,
        )
        await coord.async_start_game(room.code, host_token=host_tok)
        coord.schedule_auto_reveal(room.code, room.current_index, delay=0.15)
        assert room.code in coord._room_timers

        # Host pauses before auto-reveal fires -> timer must be cancelled and stay in PHASE_QUESTION
        await coord.async_pause_room(room.code, paused=True, host_token=host_tok)
        await asyncio.sleep(0.22)
        assert room.phase == PHASE_QUESTION
        assert room.paused is True
        await coord.async_shutdown()

    asyncio.run(run())
