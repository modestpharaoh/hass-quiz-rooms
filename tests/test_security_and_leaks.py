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
    QuizRoomsAdminTVView,
    QuizRoomsPlayView,
    QuizRoomsPublicAvatarUploadView,
    QuizRoomsPublicTVModeView,
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
        # Unauthenticated public request to /quizrooms/avatar/... must be stealth-redirected by middleware
        blocked = await middleware(
            FakeRequest(path="/quizrooms/avatar/preset/fox", remote="203.0.113.5"), ok_handler
        )
        assert blocked.status == 302
        assert blocked.headers.get("Location") == "/"

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

        # Authenticated remote HA user still redirected on /quizrooms/play when allow_external_access=False
        blocked_play = await middleware(
            FakeRequest(path="/quizrooms/play", remote="203.0.113.5", hass_user=admin), ok_handler
        )
        assert blocked_play.status == 302
        assert blocked_play.headers.get("Location") == "/"

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


def test_session_tokens_redacted_for_other_players_and_host_override_accepts_public_id(
    tmp_path: Path,
) -> None:
    coord = QuizRoomsCoordinator(
        None,
        builtin_packs_dir=BUILTIN_PACKS_DIR,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=FRONTEND_DIR,
    )
    room, host_sess, host_tok = coord.engine.create_room(
        host_session_token="secret_host_tok",
        host_nickname="Host",
        pack_ids=["general_knowledge_en"],
        round_count=5,
        answer_mode="free_text",
    )
    _, _, guest_sess = coord.engine.join_room(
        room.code, session_token="secret_guest_tok", nickname="Guest"
    )

    # Serialized state for Guest must NOT expose Host's secret_host_tok
    guest_view = coord.engine.serialize_room_state(room, for_session_token=guest_sess)
    tokens_seen_by_guest = {p["session_token"] for p in guest_view["players"]}
    assert "secret_guest_tok" in tokens_seen_by_guest
    assert "secret_host_tok" not in tokens_seen_by_guest

    # Serialized state for TV (for_session_token=None) must redact ALL secret session tokens
    tv_view = coord.engine.serialize_room_state(room, for_session_token=None)
    tokens_seen_by_tv = {p["session_token"] for p in tv_view["players"]}
    assert "secret_host_tok" not in tokens_seen_by_tv
    assert "secret_guest_tok" not in tokens_seen_by_tv
    assert all(tok.startswith("plr_") for tok in tokens_seen_by_tv)

    # Host override works using the redacted public player_id seen in Host's reveal payload
    coord.engine.start_game(room.code, host_token=host_tok)
    coord.engine.submit_answer(room.code, session_token=guest_sess, answer="wrong answer", wager=2)
    coord.engine.reveal_current_question(room.code)
    host_view = coord.engine.serialize_room_state(room, for_session_token=host_sess)
    guest_row_for_host = next(
        o for o in host_view["reveal"]["outcomes"] if o["nickname"] == "Guest"
    )
    assert guest_row_for_host["session_token"] != "secret_guest_tok"
    assert guest_row_for_host["session_token"].startswith("plr_")

    # Host passes that redacted plr_... ID to override_free_text_answer -> succeeds!
    coord.engine.override_free_text_answer(
        room.code,
        target_session_token=guest_row_for_host["session_token"],
        approve=True,
        host_token=host_tok,
    )
    assert room.players[guest_sess].score == 2


def test_private_room_tv_follow_protection_cswsh_origin_check_and_url_hardening(
    tmp_path: Path,
) -> None:
    from custom_components.quizrooms.tv_launcher import is_safe_http_url

    coord = QuizRoomsCoordinator(
        None,
        builtin_packs_dir=BUILTIN_PACKS_DIR,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=FRONTEND_DIR,
    )
    priv_room, _hs, _ht = coord.engine.create_room(
        host_session_token="priv_h",
        host_nickname="PrivHost",
        pack_ids=["general_knowledge_en"],
        visibility="private",
    )

    class _JsonReq(FakeRequest):
        def __init__(self, body: dict[str, Any], **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._body = body

        async def json(self) -> dict[str, Any]:
            return self._body

    async def run() -> None:
        # 1. Unauthenticated LAN client cannot force TV to follow a PRIVATE room (403)
        pub_tv_resp = await QuizRoomsPublicTVModeView(coord).post(
            _JsonReq(
                {"mode": "follow", "room_code": priv_room.code},
                path="/quizrooms/api/public/tv_mode",
                remote="192.168.1.30",
            )
        )
        assert pub_tv_resp.status == 403

        # 2. Authenticated HA Admin CAN follow a private room on TV (200)
        admin_tv_resp = await QuizRoomsAdminTVView(coord).post(
            _JsonReq(
                {"mode": "follow", "room_code": priv_room.code},
                path="/quizrooms/api/admin/tv",
                hass_user=SimpleNamespace(is_admin=True),
            )
        )
        assert admin_tv_resp.status == 200

        # 3. Cross-Site WebSocket Hijacking (CSWSH): mismatched external Origin header blocked (403)
        cswsh_resp = await QuizRoomsWebSocketView(coord).get(
            FakeRequest(
                path="/quizrooms/ws",
                remote="192.168.1.30",
                host="192.168.1.10:8123",
                headers={"Origin": "https://evil.example.com"},
            )
        )
        assert cswsh_resp.status == 403

        # 4. HTML pages include anti-clickjacking & referrer-policy headers
        play_resp = await QuizRoomsPlayView(coord).get(
            FakeRequest(path="/quizrooms/play", remote="192.168.1.30")
        )
        assert play_resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
        assert play_resp.headers.get("Referrer-Policy") == "same-origin"

        # 5. URL validator rejects control chars, quotes, shell metachars, credentials, and non-HTTP schemes
        assert is_safe_http_url("http://192.168.1.50:8123/quizrooms/tv") is True
        assert is_safe_http_url("javascript:alert(1)") is False
        assert is_safe_http_url("http://192.168.1.50:8123/tv\nmalicious") is False
        assert is_safe_http_url("http://192.168.1.50:8123/tv'bad") is False
        assert is_safe_http_url("http://192.168.1.50:8123/tv;reboot") is False
        assert is_safe_http_url("http://user:pass@192.168.1.50:8123/tv") is False

    asyncio.run(run())


def test_cswsh_null_origin_blocked_and_nabu_casa_x_hass_source_blocked(tmp_path: Path) -> None:
    coord = QuizRoomsCoordinator(
        None,
        builtin_packs_dir=BUILTIN_PACKS_DIR,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=FRONTEND_DIR,
    )

    # Nabu Casa snitun forwarding over 127.0.0.1 with X-Hass-Source MUST fail closed
    nabu_req = FakeRequest(
        path="/quizrooms/play",
        remote="127.0.0.1",
        peer_ip="127.0.0.1",
        headers={"X-Hass-Source": "core.remote"},
    )
    assert not coord.network_guard.evaluate_request(nabu_req).allowed

    # Malformed CF-Connecting-IP or CF-Ray without client IP over 127.0.0.1 MUST fail closed
    cf_malformed = FakeRequest(
        path="/quizrooms/play",
        remote="127.0.0.1",
        peer_ip="127.0.0.1",
        headers={"CF-Connecting-IP": "not-an-ip"},
    )
    assert not coord.network_guard.evaluate_request(cf_malformed).allowed

    cf_ray_only = FakeRequest(
        path="/quizrooms/play",
        remote="127.0.0.1",
        peer_ip="127.0.0.1",
        headers={"CF-Ray": "8a1b2c3d-FRA"},
    )
    assert not coord.network_guard.evaluate_request(cf_ray_only).allowed

    async def run() -> None:
        # Sandboxed iframe CSWSH vector sends `Origin: null` -> MUST be blocked (403)
        null_origin_resp = await QuizRoomsWebSocketView(coord).get(
            FakeRequest(
                path="/quizrooms/ws",
                remote="192.168.1.30",
                host="192.168.1.10:8123",
                headers={"Origin": "null"},
            )
        )
        assert null_origin_resp.status == 403

    asyncio.run(run())


def test_ws_connection_limits_and_slow_consumer_eviction(tmp_path: Path) -> None:
    from custom_components.quizrooms import coordinator as coord_mod
    from custom_components.quizrooms.const import MAX_WS_CLIENTS_PER_IP

    coord = QuizRoomsCoordinator(
        None,
        builtin_packs_dir=BUILTIN_PACKS_DIR,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=FRONTEND_DIR,
    )

    class _DummyWS:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    # Fill per-IP quota
    for _ in range(MAX_WS_CLIENTS_PER_IP):
        coord.register_ws(WSClient(ws=_DummyWS(), role="lobby", client_ip="192.168.1.99"))

    assert coord.can_accept_ws("192.168.1.99") is False
    assert coord.can_accept_ws("192.168.1.100") is True

    async def run() -> None:
        # Excess connection from saturated IP returns 429 before upgrading WebSocket
        over_resp = await QuizRoomsWebSocketView(coord).get(
            FakeRequest(
                path="/quizrooms/ws",
                remote="192.168.1.99",
                host="192.168.1.10:8123",
            )
        )
        assert over_resp.status == 429

        # Slow-consumer / stalled socket must time out and be evicted + closed without hanging broadcast
        class _HungWS:
            def __init__(self) -> None:
                self.closed = False

            async def send_json(self, _payload: dict[str, Any]) -> None:
                await asyncio.sleep(30.0)

            async def close(self) -> None:
                self.closed = True

        hung_ws = _HungWS()
        hung_client = WSClient(ws=hung_ws, role="lobby", client_ip="192.168.1.101")
        coord.register_ws(hung_client)

        orig_timeout = coord_mod.WS_SEND_TIMEOUT_SECONDS
        coord_mod.WS_SEND_TIMEOUT_SECONDS = 0.05
        try:
            await coord._safe_send(hung_client, {"type": "ping"})
            await asyncio.sleep(0.01)
            assert hung_client not in coord._ws_clients
            assert hung_ws.closed is True
        finally:
            coord_mod.WS_SEND_TIMEOUT_SECONDS = orig_timeout
            await coord.async_shutdown()

    asyncio.run(run())


def test_bounded_json_svg_xss_rejection_and_host_header_sanitization(tmp_path: Path) -> None:
    import io
    import zipfile
    from custom_components.quizrooms.const import MAX_JSON_BODY_BYTES
    from custom_components.quizrooms.packs import PackValidationError

    coord = QuizRoomsCoordinator(
        None,
        builtin_packs_dir=BUILTIN_PACKS_DIR,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=FRONTEND_DIR,
    )

    # Forged Host header must never poison resolve_lan_base_url
    bad_host_req = FakeRequest(
        path="/quizrooms/api/public/bootstrap",
        remote="192.168.1.20",
        host='evil.example"><script>alert(1)</script>',
    )
    assert coord.resolve_lan_base_url(bad_host_req) == ""
    good_host_req = FakeRequest(
        path="/quizrooms/api/public/bootstrap",
        remote="192.168.1.20",
        host="192.168.1.10:8123",
    )
    assert coord.resolve_lan_base_url(good_host_req) == "http://192.168.1.10:8123"

    # Uploaded ZIP pack with <script>, inline onload=, or <!ENTITY> in SVG must be rejected
    def _build_svg_zip(svg_body: str) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "pack.json",
                json.dumps(
                    {
                        "pack_id": "bad_svg_pack",
                        "title": "Bad SVG Pack",
                        "cover": "media/cover.svg",
                        "questions": [
                            {
                                "id": "q1",
                                "prompt": "Test?",
                                "choices": ["A", "B"],
                                "answer": "A",
                            }
                        ],
                    }
                ),
            )
            zf.writestr("media/cover.svg", svg_body)
        return buf.getvalue()

    for bad_svg in (
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
        '<!DOCTYPE svg [<!ENTITY lol "boom">]><svg xmlns="http://www.w3.org/2000/svg"></svg>',
    ):
        with pytest.raises(PackValidationError) as exc_svg:
            coord.pack_registry.import_pack_bytes("bad.zip", _build_svg_zip(bad_svg), overwrite=True)
        assert exc_svg.value.code == "unsafe_svg"

    class _OversizedJsonReq(FakeRequest):
        content_length = MAX_JSON_BODY_BYTES + 1024

        async def read(self) -> bytes:  # pragma: no cover
            raise AssertionError("Oversized JSON should be rejected via Content-Length")

    async def run() -> None:
        resp_pub = await QuizRoomsPublicTVModeView(coord).post(
            _OversizedJsonReq(path="/quizrooms/api/public/tv_mode", remote="192.168.1.25")
        )
        assert resp_pub.status == 413

        resp_adm = await QuizRoomsAdminTVView(coord).post(
            _OversizedJsonReq(
                path="/quizrooms/api/admin/tv",
                hass_user=SimpleNamespace(is_admin=True),
            )
        )
        assert resp_adm.status == 413

    asyncio.run(run())


def test_cross_room_ws_isolation_lobby_leave_cleanup_and_avatar_hoarding_guard(
    tmp_path: Path,
) -> None:
    coord = QuizRoomsCoordinator(
        None,
        builtin_packs_dir=BUILTIN_PACKS_DIR,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=FRONTEND_DIR,
    )
    r1, _h1_sess, h1_tok = coord.engine.create_room(
        host_session_token="host_r1",
        host_nickname="HostR1",
        host_avatar="preset:fox",
        pack_ids=["general_knowledge_en"],
        round_count=5,
    )
    r2, _h2_sess, h2_tok = coord.engine.create_room(
        host_session_token="host_r2",
        host_nickname="HostR2",
        host_avatar="preset:owl",
        pack_ids=["general_knowledge_en"],
        round_count=5,
    )

    # Guest joins r1 with preset:astro, then joins r2 with preset:dragon using SAME session_token:
    # preset:astro must be released rather than hoarded!
    coord.engine.join_room(r1.code, session_token="roamer", nickname="Roamer", avatar="preset:astro")
    assert "astro" in coord.engine.get_taken_preset_avatars()
    coord.engine.join_room(r2.code, session_token="roamer", nickname="Roamer", avatar="preset:dragon")
    taken = coord.engine.get_taken_preset_avatars()
    assert "dragon" in taken
    assert "astro" not in taken

    # Leaving an unstarted Lobby removes non-host guest from room.players immediately
    coord.engine.leave_room(r1.code, "roamer")
    assert "roamer" not in r1.players

    class _CaptureWS:
        def __init__(self) -> None:
            self.sent: list[dict[str, Any]] = []

        async def send_json(self, payload: dict[str, Any]) -> None:
            self.sent.append(payload)

    async def run() -> None:
        await coord.async_start_game(r1.code, host_token=h1_tok)
        await coord.async_start_game(r2.code, host_token=h2_tok)
        ws_view = QuizRoomsWebSocketView(coord)
        fake_ws = _CaptureWS()
        bound_client = WSClient(
            ws=fake_ws, role="room", room_code=r2.code, session_token="roamer"
        )
        # Attempt to submit an answer into r1 while socket is bound to r2 -> room_mismatch error!
        await ws_view._handle_message(
            bound_client,
            json.dumps(
                {
                    "type": "submit_answer",
                    "code": r1.code,
                    "answer": r1.questions[0].choices[0],
                    "wager": 1,
                }
            ),
        )
        assert fake_ws.sent and fake_ws.sent[-1]["code"] == "room_mismatch"
        assert "roamer" not in r1.current_submissions
        await coord.async_shutdown()

    asyncio.run(run())
