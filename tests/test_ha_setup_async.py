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
    QuizRoomsTVView,
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
        assert len(coord.pack_registry.list_packs()) == 3

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
            round_count=2,
        )
        before_start = len(hass.executor_calls)
        await coord.async_start_game(room.code, host_token=host_tok)
        assert len(hass.executor_calls) == before_start + 1
        assert len(room.pinned_media) == 2

        # 4. Reload packs runs via executor
        before_reload = len(hass.executor_calls)
        await coord.async_reload_packs()
        assert len(hass.executor_calls) == before_reload + 1

        # 5. Clean unload
        assert await async_unload_entry(hass, entry) is True

    asyncio.run(run())
