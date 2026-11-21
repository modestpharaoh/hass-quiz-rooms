"""Pytest configuration and Home Assistant / aiohttp compatibility shims for offline testing."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
import types
from typing import Any

import pytest

# Ensure repository root is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _install_test_stubs() -> None:
    """Install lightweight stubs for aiohttp, voluptuous, and homeassistant when absent."""
    if "aiohttp" not in sys.modules:
        aiohttp_mod = types.ModuleType("aiohttp")
        web_mod = types.ModuleType("aiohttp.web")

        class WSMsgType:
            TEXT = 1
            ERROR = 258

        class Response:
            def __init__(
                self,
                *,
                status: int = 200,
                text: str | None = None,
                body: bytes | None = None,
                content_type: str | None = None,
                headers: dict[str, str] | None = None,
            ) -> None:
                self.status = status
                self.text = text
                self.body = body if body is not None else (text.encode("utf-8") if text is not None else b"")
                self.headers: dict[str, str] = dict(headers or {})
                if content_type and "Content-Type" not in self.headers:
                    self.headers["Content-Type"] = content_type
                self.content_type = self.headers.get("Content-Type", content_type or "")

        def json_response(data: Any, *, status: int = 200, headers: dict[str, str] | None = None) -> Response:
            return Response(
                status=status,
                text=json.dumps(data),
                content_type="application/json",
                headers=headers,
            )

        def middleware(fn):
            return fn

        web_mod.Response = Response
        web_mod.StreamResponse = Response
        web_mod.Request = Any
        web_mod.WebSocketResponse = object
        web_mod.json_response = json_response
        web_mod.middleware = middleware

        aiohttp_mod.web = web_mod
        aiohttp_mod.WSMsgType = WSMsgType
        sys.modules["aiohttp"] = aiohttp_mod
        sys.modules["aiohttp.web"] = web_mod

    if "voluptuous" not in sys.modules:
        vol_mod = types.ModuleType("voluptuous")
        vol_mod.Schema = lambda s: s
        vol_mod.Required = lambda k, **kw: k
        vol_mod.Optional = lambda k, **kw: k
        vol_mod.In = lambda vals: vals
        sys.modules["voluptuous"] = vol_mod

    if "homeassistant" not in sys.modules:
        ha = types.ModuleType("homeassistant")
        ha_core = types.ModuleType("homeassistant.core")
        ha_exc = types.ModuleType("homeassistant.exceptions")
        ha_ce = types.ModuleType("homeassistant.config_entries")
        ha_comp = types.ModuleType("homeassistant.components")
        ha_http = types.ModuleType("homeassistant.components.http")
        ha_num = types.ModuleType("homeassistant.components.number")
        ha_sens = types.ModuleType("homeassistant.components.sensor")
        ha_fe = types.ModuleType("homeassistant.components.frontend")
        ha_pc = types.ModuleType("homeassistant.components.panel_custom")
        ha_helpers = types.ModuleType("homeassistant.helpers")
        ha_ep = types.ModuleType("homeassistant.helpers.entity_platform")
        ha_rs = types.ModuleType("homeassistant.helpers.restore_state")

        ha_core.HomeAssistant = Any
        ha_core.ServiceCall = Any
        ha_core.callback = lambda fn: fn

        class HomeAssistantError(Exception):
            pass

        ha_exc.HomeAssistantError = HomeAssistantError

        class ConfigFlow:
            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__()

        class OptionsFlow:
            pass

        ha_ce.ConfigEntry = Any
        ha_ce.ConfigFlow = ConfigFlow
        ha_ce.OptionsFlow = OptionsFlow
        ha_ce.ConfigFlowResult = dict

        class HomeAssistantView:
            url = ""
            name = ""
            requires_auth = True

        ha_http.HomeAssistantView = HomeAssistantView

        class NumberMode:
            BOX = "box"

        class _BaseEntity:
            async def async_added_to_hass(self) -> None:
                return None

            def async_write_ha_state(self) -> None:
                return None

        class NumberEntity(_BaseEntity):
            _attr_native_min_value: float = 0.0
            _attr_native_max_value: float = 100.0
            _attr_native_step: float = 1.0

            @property
            def native_min_value(self) -> float:
                return self._attr_native_min_value

            @property
            def native_max_value(self) -> float:
                return self._attr_native_max_value

            @property
            def native_step(self) -> float:
                return self._attr_native_step

        class RestoreEntity(_BaseEntity):
            _mock_last_state: Any = None

            async def async_get_last_state(self) -> Any:
                return self._mock_last_state

        class SensorEntity(_BaseEntity):
            pass

        class SensorStateClass:
            MEASUREMENT = "measurement"

        ha_num.NumberEntity = NumberEntity
        ha_num.NumberMode = NumberMode
        ha_sens.SensorEntity = SensorEntity
        ha_sens.SensorStateClass = SensorStateClass
        ha_ep.AddEntitiesCallback = Any
        ha_rs.RestoreEntity = RestoreEntity
        ha_fe.async_remove_panel = lambda hass, path: None

        async def _noop_panel(*args, **kwargs):
            return None

        ha_pc.async_register_panel = _noop_panel
        ha_comp.frontend = ha_fe
        ha_comp.panel_custom = ha_pc
        ha_comp.http = ha_http
        ha_comp.number = ha_num
        ha_comp.sensor = ha_sens

        for name, mod in [
            ("homeassistant", ha),
            ("homeassistant.core", ha_core),
            ("homeassistant.exceptions", ha_exc),
            ("homeassistant.config_entries", ha_ce),
            ("homeassistant.components", ha_comp),
            ("homeassistant.components.http", ha_http),
            ("homeassistant.components.number", ha_num),
            ("homeassistant.components.sensor", ha_sens),
            ("homeassistant.components.frontend", ha_fe),
            ("homeassistant.components.panel_custom", ha_pc),
            ("homeassistant.helpers", ha_helpers),
            ("homeassistant.helpers.entity_platform", ha_ep),
            ("homeassistant.helpers.restore_state", ha_rs),
        ]:
            sys.modules[name] = mod


_install_test_stubs()


class FakeTransport:
    def __init__(self, peer_ip: str | None) -> None:
        self._peer_ip = peer_ip

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        if name == "peername" and self._peer_ip is not None:
            return (self._peer_ip, 54321)
        return default


@dataclass
class FakeRequest:
    """Minimal aiohttp-like Request object for testing NetworkGuard and Views."""

    path: str = "/quizrooms/play"
    remote: str | None = "192.168.1.50"
    headers: dict[str, str] = field(default_factory=dict)
    peer_ip: str | None = None
    scheme: str = "http"
    host: str = "192.168.1.10:8123"
    query: dict[str, str] = field(default_factory=dict)
    hass_user: Any = None

    def __post_init__(self) -> None:
        self.transport = FakeTransport(self.peer_ip if self.peer_ip is not None else self.remote)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "hass_user":
            return self.hass_user
        return default


class FakeClock:
    def __init__(self, initial: float = 1_700_000_000.0) -> None:
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def builtin_packs_dir() -> Path:
    return REPO_ROOT / "custom_components" / "quizrooms" / "packs"


@pytest.fixture
def pack_registry(tmp_path: Path, builtin_packs_dir: Path):
    from custom_components.quizrooms.packs import PackRegistry

    reg = PackRegistry(builtin_packs_dir, tmp_path / "custom_packs")
    reg.reload()
    return reg


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()
