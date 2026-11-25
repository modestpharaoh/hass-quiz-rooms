"""Tests verifying HA async_setup_entry with frozen aiohttp app and zero blocking I/O on event loop."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from custom_components.quizrooms import async_setup_entry, async_unload_entry
from custom_components.quizrooms.const import DOMAIN
from custom_components.quizrooms.views import (
    QuizRoomsAdminPanelJSView,
    QuizRoomsAssetsView,
    QuizRoomsMediaView,
    QuizRoomsPlayView,
    QuizRoomsPublicBootstrapView,
    QuizRoomsPublicTVModeView,
    QuizRoomsTVView,
    QuizRoomsWebSocketView,
)
from tests.conftest import FakeRequest


class _FrozenMiddlewareList(list):
    """Simulates aiohttp's FrozenList after hass.http.app has started."""

    frozen = True

    def insert(self, index: int, obj: Any) -> None:  # pragma: no cover - must never be called
        raise RuntimeError("Cannot modify frozen list.")

    def append(self, obj: Any) -> None:  # pragma: no cover - must never be called
        raise RuntimeError("Cannot modify frozen list.")


class _FakeConfig:
    def __init__(self, root: Path) -> None:
        self._root = root
        self.internal_url = "http://192.168.1.50:8123"
        self.external_url = None

    def path(self, *parts: str) -> str:
        return str(self._root.joinpath(*parts))


class _FakeServices:
    def __init__(self) -> None:
        self.registered: dict[tuple[str, str], Any] = {}

    def async_register(self, domain: str, service: str, handler: Any, schema: Any = None) -> None:
        self.registered[(domain, service)] = handler

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.registered

    def async_remove(self, domain: str, service: str) -> None:
        self.registered.pop((domain, service), None)


class _FakeConfigEntries:
    async def async_forward_entry_setups(self, entry: Any, platforms: list[str]) -> bool:
        return True

    async def async_unload_platforms(self, entry: Any, platforms: list[str]) -> bool:
        return True


class _FakeEntry:
    def __init__(self) -> None:
        self.entry_id = "entry_quizrooms_1"
        self.options: dict[str, Any] = {}
        self._unload_cbs: list[Any] = []

    def add_update_listener(self, cb: Any) -> Any:
        return lambda: None

    def async_on_unload(self, cb: Any) -> None:
        self._unload_cbs.append(cb)


class _FakeHass:
    """Simulates a live Home Assistant instance with a frozen HTTP app and executor tracking."""

    def __init__(self, config_dir: Path) -> None:
        self.data: dict[str, Any] = {}
        self.config = _FakeConfig(config_dir)
        self.services = _FakeServices()
        self.config_entries = _FakeConfigEntries()
        self.executor_calls: list[str] = []
        self.registered_views: list[Any] = []

        class _FakeApp:
            frozen = True
            middlewares = _FrozenMiddlewareList()

        class _FakeHTTP:
            trusted_proxies: list[Any] = []
            use_x_forwarded_for = True
            app = _FakeApp()

            def __init__(self, sink: list[Any]) -> None:
                self._sink = sink

            def register_view(self, view: Any) -> None:
                self._sink.append(view)

        self.http = _FakeHTTP(self.registered_views)

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        self.executor_calls.append(getattr(func, "__name__", repr(func)))
        return await asyncio.to_thread(func, *args)


def test_setup_entry_handles_frozen_app_and_offloads_all_file_io(tmp_path: Path) -> None:
    hass = _FakeHass(tmp_path)
    entry = _FakeEntry()

    async def run() -> None:
        # 1. async_setup_entry must NOT raise RuntimeError("Cannot modify frozen list.")
        #    and MUST load packs via async_add_executor_job.
        ok = await async_setup_entry(hass, entry)
        assert ok is True
        assert DOMAIN in hass.data
        assert len(hass.executor_calls) >= 1

        coord = hass.data[DOMAIN]["coordinator"]
        assert len(coord.pack_registry.list_packs()) == 48

        # 2. All file-serving views offload reads to hass.async_add_executor_job
        before = len(hass.executor_calls)
        play_resp = await QuizRoomsPlayView(coord).get(FakeRequest(path="/quizrooms/play"))
        assert play_resp.status == 200 and "QuizRooms" in (play_resp.text or "")

        tv_resp = await QuizRoomsTVView(coord).get(FakeRequest(path="/quizrooms/tv"))
        assert tv_resp.status == 200 and "QuizRooms" in (tv_resp.text or "")

        asset_resp = await QuizRoomsAssetsView(coord).get(
            FakeRequest(path="/quizrooms/assets/css/theme.css"), "css/theme.css"
        )
        assert asset_resp.status == 200

        admin_js = await QuizRoomsAdminPanelJSView(coord).get(
            FakeRequest(path="/quizrooms/admin-panel.js")
        )
        assert admin_js.status == 200

        flags = coord.pack_registry.get_pack("countries_flags")
        assert flags is not None
        mid = flags.questions[0].media_id
        assert mid is not None
        media_resp = await QuizRoomsMediaView(coord).get(
            FakeRequest(path=f"/quizrooms/media/{mid}"), mid
        )
        assert media_resp.status == 200
        # 5 views -> 5 executor calls (zero blocking I/O on the event loop!)
        assert len(hass.executor_calls) == before + 5

        # 3. Starting a game with photo/flag media pins bytes via executor
        room, _sess, host_tok = coord.engine.create_room(
            host_session_token="host_async",
            host_nickname="AsyncHost",
            pack_ids=["countries_flags"],
            round_count=5,
        )
        before_start = len(hass.executor_calls)
        await coord.async_start_game(room.code, host_token=host_tok)
        assert len(hass.executor_calls) == before_start + 1
        assert len(room.pinned_media) == 5

        # 4. Reload packs runs via executor
        before_reload = len(hass.executor_calls)
        await coord.async_reload_packs()
        assert len(hass.executor_calls) == before_reload + 1

        # 5. Clean unload
        assert await async_unload_entry(hass, entry) is True

    asyncio.run(run())


def test_websocket_lifecycle_create_and_start_room(tmp_path: Path, monkeypatch) -> None:
    """Verify WSClient hashing, hello handshake, create_room, start_game, and disconnect."""
    import json
    from aiohttp import WSMsgType, web

    hass = _FakeHass(tmp_path)
    entry = _FakeEntry()

    class _FakeWSMsg:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.type = WSMsgType.TEXT
            self.data = json.dumps(payload)

    class _FakeWS:
        def __init__(self, incoming: list[dict[str, Any]]) -> None:
            self._incoming = list(incoming)
            self.sent: list[dict[str, Any]] = []
            self.prepared = False

        async def prepare(self, request: Any) -> None:
            self.prepared = True

        async def send_json(self, payload: dict[str, Any]) -> None:
            self.sent.append(payload)

        async def close(self) -> None:
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._incoming:
                raise StopAsyncIteration
            return _FakeWSMsg(self._incoming.pop(0))

    async def run() -> None:
        await async_setup_entry(hass, entry)
        coord = hass.data[DOMAIN]["coordinator"]

        ws_instance = _FakeWS(
            [
                {
                    "type": "create_room",
                    "session_token": "browser_tok_1",
                    "nickname": "PlayerOne",
                    "room_name": "Studio Night",
                    "pack_ids": ["countries_flags"],
                    "round_count": 5,
                    "timer_seconds": 30,
                    "answer_mode": "multiple_choice",
                    "wager_mode": "pool",
                    "deduct_wrong_wager": True,
                    "visibility": "public",
                    "allow_late_join": True,
                }
            ]
        )
        monkeypatch.setattr(web, "WebSocketResponse", lambda **kw: ws_instance)

        ws_view = QuizRoomsWebSocketView(coord)
        await ws_view.get(FakeRequest(path="/quizrooms/ws", query={"role": "lobby"}))

        assert ws_instance.prepared is True
        types_sent = [m["type"] for m in ws_instance.sent]
        assert "hello" in types_sent
        assert "room_created" in types_sent
        hello_msg = next(m for m in ws_instance.sent if m["type"] == "hello")
        assert len(hello_msg["packs"]) == 48
        created_msg = next(m for m in ws_instance.sent if m["type"] == "room_created")
        assert created_msg["code"] in coord.engine.rooms
        assert created_msg["host_token"]
        assert len(coord._ws_clients) == 0  # cleaned up on disconnect

        await async_unload_entry(hass, entry)

    asyncio.run(run())


def test_tv_live_overview_updates_and_follow_mode(tmp_path: Path) -> None:
    """Verify TV WS receives live lobby_update on create/close and Follow mode works via WS & HTTP."""
    import json
    from custom_components.quizrooms.coordinator import WSClient

    hass = _FakeHass(tmp_path)
    entry = _FakeEntry()

    class _RecordingWS:
        def __init__(self) -> None:
            self.sent: list[dict[str, Any]] = []

        async def send_json(self, payload: dict[str, Any]) -> None:
            self.sent.append(payload)

        async def close(self) -> None:
            return None

    class _JsonRequest(FakeRequest):
        def __init__(self, body: dict[str, Any], **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._body = body

        async def json(self) -> dict[str, Any]:
            return self._body

    async def run() -> None:
        await async_setup_entry(hass, entry)
        coord = hass.data[DOMAIN]["coordinator"]

        tv_ws = _RecordingWS()
        tv_client = WSClient(ws=tv_ws, role="tv")
        coord.register_ws(tv_client)

        # 1. Creating a room pushes lobby_update to TV Overview immediately
        room, _sess, host_tok = coord.engine.create_room(
            host_session_token="h_tv",
            host_nickname="HostTV",
            pack_ids=["general_knowledge_en"],
        )
        await coord.broadcast_lobby_and_tv()
        assert any(
            m["type"] == "lobby_update" and any(r["code"] == room.code for r in m["lobby"]["rooms"])
            for m in tv_ws.sent
        )

        # 2. HTTP TV mode fallback follows the room (and stealth-redirects public IPs)
        tv_mode_view = QuizRoomsPublicTVModeView(coord)
        blocked = await tv_mode_view.post(
            _JsonRequest({"mode": "follow", "room_code": room.code}, path="/quizrooms/api/public/tv_mode", remote="203.0.113.5")
        )
        assert blocked.status == 302
        assert blocked.headers.get("Location") == "/"

        ok = await tv_mode_view.post(
            _JsonRequest({"mode": "follow", "room_code": room.code}, path="/quizrooms/api/public/tv_mode", remote="192.168.1.20")
        )
        assert ok.status == 200
        body = json.loads(ok.text or "{}")
        assert body["tv"]["mode"] == "follow"
        assert body["room"]["code"] == room.code
        assert any(m["type"] == "tv_mode_changed" and m["room"]["code"] == room.code for m in tv_ws.sent)

        # 3. Bootstrap includes the followed room while active
        boot_resp = await QuizRoomsPublicBootstrapView(coord).get(
            FakeRequest(path="/quizrooms/api/public/bootstrap", remote="192.168.1.20")
        )
        boot_data = json.loads(boot_resp.text or "{}")
        assert boot_data["tv"]["mode"] == "follow"
        assert boot_data["room"]["code"] == room.code

        # 4. Closing the room returns TV to overview and removes it from live lobby_update
        tv_ws.sent.clear()
        await coord.async_close_room(room.code, host_token=host_tok)
        assert coord.tv_state.mode == "overview"
        assert any(m["type"] == "tv_mode_changed" and m["tv"]["mode"] == "overview" for m in tv_ws.sent)
        assert any(m["type"] == "lobby_update" and m["lobby"]["rooms"] == [] for m in tv_ws.sent)

        await async_unload_entry(hass, entry)

    asyncio.run(run())
