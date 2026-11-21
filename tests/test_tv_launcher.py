"""Tests for discovering supported Smart TV media players and opening the TV dashboard."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.quizrooms.coordinator import QuizRoomsCoordinator
from custom_components.quizrooms.tv_launcher import (
    TVLaunchError,
    async_open_tv_dashboard_on_player,
    discover_supported_tv_players,
)
from custom_components.quizrooms.views import (
    QuizRoomsAdminStateView,
    QuizRoomsAdminTVOpenView,
)
from tests.conftest import FakeRequest

BUILTIN_PACKS_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "quizrooms" / "packs"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "quizrooms" / "frontend"


@dataclass
class _RegEntry:
    entity_id: str
    domain: str
    platform: str
    name: str | None = None
    original_name: str | None = None
    device_id: str | None = None
    disabled_by: str | None = None


@dataclass
class _StateObj:
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)


class _FakeEntityRegistry:
    def __init__(self, entries: list[_RegEntry]) -> None:
        self.entities = {e.entity_id: e for e in entries}

    def async_get(self, entity_id: str) -> _RegEntry | None:
        return self.entities.get(entity_id)


class _FakeStates:
    def __init__(self, mapping: dict[str, _StateObj]) -> None:
        self._map = mapping

    def get(self, entity_id: str) -> _StateObj | None:
        return self._map.get(entity_id)


class _FakeServices:
    def __init__(self, failing_calls: set[tuple[str, str, str]] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], bool]] = []
        self._failing = failing_calls or set()

    async def async_call(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        blocking: bool = False,
    ) -> None:
        key = (domain, service, str(data.get("media_content_type") or data.get("command") or ""))
        if key in self._failing:
            raise RuntimeError(f"Simulated failure for {key}")
        self.calls.append((domain, service, dict(data), blocking))


def _make_hass(
    entries: list[_RegEntry],
    states: dict[str, _StateObj],
    failing_calls: set[tuple[str, str, str]] | None = None,
) -> Any:
    return SimpleNamespace(
        data={"entity_registry": _FakeEntityRegistry(entries)},
        states=_FakeStates(states),
        services=_FakeServices(failing_calls=failing_calls),
        config=SimpleNamespace(internal_url="http://192.168.1.50:8123", external_url=None),
    )


def test_discover_only_connected_supported_smart_tv_media_players() -> None:
    entries = [
        _RegEntry("media_player.lg_living", "media_player", "webostv", device_id="d1"),
        _RegEntry("media_player.shield_adb", "media_player", "androidtv", device_id="d2"),
        _RegEntry("media_player.google_tv", "media_player", "androidtv_remote", device_id="d3"),
        _RegEntry("remote.google_tv", "remote", "androidtv_remote", device_id="d3"),
        _RegEntry("media_player.samsung_qled", "media_player", "samsungtv", device_id="d4"),
        _RegEntry("media_player.samsung_frame", "media_player", "samsungtv_smart", device_id="d5"),
        # Unsupported or disconnected media players that MUST be filtered out:
        _RegEntry("media_player.sonos_kitchen", "media_player", "sonos", device_id="d6"),
        _RegEntry("media_player.spotify_phone", "media_player", "spotify", device_id="d7"),
        _RegEntry("media_player.lg_offline", "media_player", "webostv", device_id="d8"),
        _RegEntry("media_player.samsung_disabled", "media_player", "samsungtv", disabled_by="user"),
    ]
    states = {
        "media_player.lg_living": _StateObj("on", {"friendly_name": "Living Room LG"}),
        "media_player.shield_adb": _StateObj("idle", {"friendly_name": "Shield TV"}),
        "media_player.google_tv": _StateObj("off", {"friendly_name": "Bedroom Google TV"}),
        "media_player.samsung_qled": _StateObj("playing", {"friendly_name": "Samsung QLED"}),
        "media_player.samsung_frame": _StateObj("on", {"friendly_name": "Samsung Frame"}),
        "media_player.sonos_kitchen": _StateObj("playing", {"friendly_name": "Sonos Kitchen"}),
        "media_player.spotify_phone": _StateObj("playing", {"friendly_name": "Spotify"}),
        "media_player.lg_offline": _StateObj("unavailable", {"friendly_name": "Offline LG"}),
        "media_player.samsung_disabled": _StateObj("on", {"friendly_name": "Disabled Samsung"}),
    }
    hass = _make_hass(entries, states)
    discovered = discover_supported_tv_players(hass)
    found_ids = [t["entity_id"] for t in discovered]

    assert set(found_ids) == {
        "media_player.lg_living",
        "media_player.shield_adb",
        "media_player.google_tv",
        "media_player.samsung_qled",
        "media_player.samsung_frame",
    }
    # Sonos, Spotify, unavailable LG, disabled Samsung, and remote.* must NOT appear
    assert "media_player.sonos_kitchen" not in found_ids
    assert "media_player.spotify_phone" not in found_ids
    assert "media_player.lg_offline" not in found_ids
    assert "media_player.samsung_disabled" not in found_ids
    assert "remote.google_tv" not in found_ids
    # Active/on TVs sorted ahead of standby/off TVs
    assert found_ids[-1] == "media_player.google_tv"


def test_open_tv_dashboard_dispatches_platform_native_commands_and_fallbacks() -> None:
    tv_url = "http://192.168.1.50:8123/quizrooms/tv"
    entries = [
        _RegEntry("media_player.lg_tv", "media_player", "webostv", device_id="d1"),
        _RegEntry("media_player.android_adb", "media_player", "androidtv", device_id="d2"),
        _RegEntry("media_player.android_rem", "media_player", "androidtv_remote", device_id="d3"),
        _RegEntry("remote.android_rem", "remote", "androidtv_remote", device_id="d3"),
        _RegEntry("media_player.samsung_std", "media_player", "samsungtv", device_id="d4"),
        _RegEntry("media_player.samsung_smart", "media_player", "samsungtv_smart", device_id="d5"),
        _RegEntry("media_player.sonos", "media_player", "sonos", device_id="d6"),
    ]
    states = {
        e.entity_id: _StateObj("on", {"friendly_name": e.entity_id})
        for e in entries
        if e.domain == "media_player"
    }

    async def run() -> None:
        # 1. LG webOS -> webostv.command system.launcher/open
        hass_lg = _make_hass(entries, states)
        res = await async_open_tv_dashboard_on_player(hass_lg, "media_player.lg_tv", tv_url)
        assert res["method"] == "webostv.command"
        assert hass_lg.services.calls == [
            (
                "webostv",
                "command",
                {
                    "entity_id": "media_player.lg_tv",
                    "command": "system.launcher/open",
                    "payload": {"target": tv_url},
                },
                True,
            )
        ]

        # 2. Android TV ADB -> androidtv.adb_command VIEW intent
        hass_adb = _make_hass(entries, states)
        res = await async_open_tv_dashboard_on_player(hass_adb, "media_player.android_adb", tv_url)
        assert res["method"] == "androidtv.adb_command"
        domain, svc, payload, _ = hass_adb.services.calls[0]
        assert (domain, svc) == ("androidtv", "adb_command")
        assert "android.intent.action.VIEW" in payload["command"]
        assert tv_url in payload["command"]

        # 3. Android TV Remote -> fallback to sibling remote.turn_on when play_media fails
        hass_rem = _make_hass(entries, states, failing_calls={("media_player", "play_media", "url")})
        res = await async_open_tv_dashboard_on_player(hass_rem, "media_player.android_rem", tv_url)
        assert res["method"] == "remote.turn_on"
        assert hass_rem.services.calls == [
            ("remote", "turn_on", {"entity_id": "remote.android_rem", "activity": tv_url}, True)
        ]

        # 4. Samsung Smart (HACS) -> uses media_content_type="browser"
        hass_ss = _make_hass(entries, states)
        res = await async_open_tv_dashboard_on_player(hass_ss, "media_player.samsung_smart", tv_url)
        assert res["method"] == "media_player.play_media:browser"
        assert hass_ss.services.calls[0][2]["media_content_type"] == "browser"
        assert hass_ss.services.calls[0][2]["media_content_id"] == tv_url

        # 5. Samsung Official -> falls back to launching org.tizen.browser if url type rejected
        hass_s = _make_hass(entries, states, failing_calls={("media_player", "play_media", "url")})
        res = await async_open_tv_dashboard_on_player(hass_s, "media_player.samsung_std", tv_url)
        assert res["method"] == "media_player.play_media:app"
        assert hass_s.services.calls[0][2]["media_content_id"] == "org.tizen.browser"

        # 6. Unsupported speaker entity is rejected
        with pytest.raises(TVLaunchError) as exc:
            await async_open_tv_dashboard_on_player(hass_lg, "media_player.sonos", tv_url)
        assert exc.value.code == "unsupported_platform"

    asyncio.run(run())


def test_admin_views_expose_tv_targets_and_open_tv_endpoint(tmp_path: Path) -> None:
    entries = [
        _RegEntry("media_player.lg_living", "media_player", "webostv", device_id="d1"),
        _RegEntry("media_player.sonos", "media_player", "sonos", device_id="d2"),
    ]
    states = {
        "media_player.lg_living": _StateObj("on", {"friendly_name": "Living Room LG"}),
        "media_player.sonos": _StateObj("on", {"friendly_name": "Sonos"}),
    }
    hass = _make_hass(entries, states)
    coord = QuizRoomsCoordinator(
        hass,
        builtin_packs_dir=BUILTIN_PACKS_DIR,
        custom_packs_dir=tmp_path / "custom",
        frontend_dir=FRONTEND_DIR,
    )

    class _JsonReq(FakeRequest):
        def __init__(self, body: dict[str, Any], **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._body = body

        async def json(self) -> dict[str, Any]:
            return self._body

    async def run() -> None:
        # Admin state includes only the supported LG TV
        admin_user = SimpleNamespace(is_admin=True)
        state_resp = await QuizRoomsAdminStateView(coord).get(
            FakeRequest(path="/quizrooms/api/admin/state", hass_user=admin_user)
        )
        assert state_resp.status == 200
        data = json.loads(state_resp.text or "{}")
        assert [t["entity_id"] for t in data["tv_targets"]] == ["media_player.lg_living"]

        # Non-admin denied on /tv/open
        denied = await QuizRoomsAdminTVOpenView(coord).post(
            _JsonReq({"entity_id": "media_player.lg_living"}, path="/quizrooms/api/admin/tv/open")
        )
        assert denied.status == 403

        # Admin succeeds opening TV dashboard on LG TV
        ok = await QuizRoomsAdminTVOpenView(coord).post(
            _JsonReq(
                {"entity_id": "media_player.lg_living"},
                path="/quizrooms/api/admin/tv/open",
                hass_user=admin_user,
            )
        )
        assert ok.status == 200
        body = json.loads(ok.text or "{}")
        assert body["ok"] is True
        assert body["url"] == "http://192.168.1.50:8123/quizrooms/tv"
        assert len(hass.services.calls) == 1

    asyncio.run(run())
