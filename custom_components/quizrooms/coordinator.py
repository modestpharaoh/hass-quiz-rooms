"""Runtime coordinator connecting QuizEngine, WebSocket hub, timers, and HA entities."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
from typing import Any, Callable

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .avatars import AvatarStore, list_preset_catalog
from .const import (
    ALLOWED_THEMES,
    ALLOWED_TV_MODES,
    CLEANUP_INTERVAL_SECONDS,
    CONF_ALLOW_EXTERNAL_ACCESS,
    CONF_ALLOWED_SUBNETS,
    CONF_CUSTOM_LAN_BASE_URL,
    CONF_STAGE_EFFECTS,
    COUNTDOWN_WARNING_SECONDS,
    CUSTOM_PACKS_REL_DIR,
    DEFAULT_ALLOW_EXTERNAL_ACCESS,
    DEFAULT_ALLOWED_SUBNETS,
    DEFAULT_CUSTOM_LAN_BASE_URL,
    DEFAULT_MAX_ROOMS,
    DOMAIN,
    EVENT_FINALE,
    EVENT_PHASE_CHANGED,
    MAX_WS_CLIENTS,
    MAX_WS_CLIENTS_PER_IP,
    PHASE_FINALE,
    PHASE_LOBBY,
    PHASE_QUESTION,
    PHASE_REVEAL,
    THEME_AUTO,
    TV_MODE_FOLLOW,
    TV_MODE_OVERVIEW,
    URL_PLAY,
    VERSION,
    VISIBILITY_PUBLIC,
    WS_SEND_TIMEOUT_SECONDS,
)
from .engine import EngineError, QuizEngine
from .network_guard import NetworkGuard
from .packs import PackRegistry
from .stage_effects import (
    StageDirector,
    StageEffectError,
    build_finale_payload,
    build_phase_changed_payload,
)
from .tv_launcher import is_safe_http_url

_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]+$")


def build_device_info(entry_id: str) -> DeviceInfo:
    """Return shared Home Assistant Service DeviceInfo grouping all QuizRooms entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name="QuizRooms",
        manufacturer="QuizRooms",
        model="Local-First Trivia Engine",
        sw_version=VERSION,
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=f"homeassistant://{DOMAIN}",
    )

_LOGGER = logging.getLogger(__name__)


@dataclass(eq=False)
class WSClient:
    """Represents a connected WebSocket client (lobby, room player, or TV display).

    Uses identity equality/hashing (eq=False) so live connections can be stored in sets.
    """

    ws: Any
    role: str  # "lobby" | "room" | "tv"
    room_code: str | None = None
    session_token: str | None = None
    client_ip: str | None = None


@dataclass
class TVState:
    """Current synchronized state of the TV dashboard."""

    mode: str = TV_MODE_OVERVIEW
    room_code: str | None = None
    theme: str | None = None  # None = let TV local preference decide; else auto/light/dark

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "room_code": self.room_code,
            "theme": self.theme,
        }


class QuizRoomsCoordinator:
    """Central runtime manager for QuizRooms inside Home Assistant."""

    def __init__(
        self,
        hass: Any,
        *,
        builtin_packs_dir: Path,
        custom_packs_dir: Path,
        frontend_dir: Path,
        options: dict[str, Any] | None = None,
        load_packs: bool = True,
        entry: Any | None = None,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self.frontend_dir = Path(frontend_dir)
        self.avatar_store = AvatarStore(self.frontend_dir)
        self.pack_registry = PackRegistry(builtin_packs_dir, custom_packs_dir)
        if load_packs:
            self.frontend_dir = self.frontend_dir.resolve()
            self.avatar_store.set_frontend_dir(self.frontend_dir)
            self.pack_registry.reload()

        self.engine = QuizEngine(self.pack_registry, max_rooms=DEFAULT_MAX_ROOMS)
        self.network_guard = NetworkGuard()
        self.stage_director = StageDirector(hass)
        self.custom_lan_base_url: str = DEFAULT_CUSTOM_LAN_BASE_URL
        self.tv_state = TVState()

        self._ws_clients: set[WSClient] = set()
        self._room_timers: dict[str, asyncio.Task[None]] = {}
        self._warning_timers: dict[str, asyncio.Task[None]] = {}
        self._cleanup_task: asyncio.Task[None] | None = None
        self._entity_listeners: list[Callable[[], None]] = []

        self.apply_options(options or {})

    @classmethod
    def from_hass(
        cls,
        hass: Any,
        options: dict[str, Any] | None = None,
        *,
        entry: Any | None = None,
    ) -> "QuizRoomsCoordinator":
        base_dir = Path(__file__).parent
        builtin_packs_dir = base_dir / "packs"
        frontend_dir = base_dir / "frontend"
        config_path = Path(hass.config.path(CUSTOM_PACKS_REL_DIR))
        return cls(
            hass,
            builtin_packs_dir=builtin_packs_dir,
            custom_packs_dir=config_path,
            frontend_dir=frontend_dir,
            options=options,
            load_packs=False,
            entry=entry,
        )

    async def async_run_in_executor(self, func: Callable[..., Any], *args: Any) -> Any:
        """Run blocking filesystem or CPU work off the asyncio event loop."""
        executor_fn = getattr(self.hass, "async_add_executor_job", None)
        if callable(executor_fn):
            return await executor_fn(func, *args)
        return await asyncio.to_thread(func, *args)

    async def async_initialize(self) -> None:
        """Perform initial non-blocking filesystem resolution and pack loading."""
        def _init_sync() -> None:
            self.frontend_dir = self.frontend_dir.resolve()
            self.avatar_store.set_frontend_dir(self.frontend_dir)
            self.pack_registry.reload()

        await self.async_run_in_executor(_init_sync)

    def is_stage_events_allowed(self) -> bool:
        """Hard LAN-Only security gate: discard HA events & stage effects when external access is enabled."""
        return not bool(self.network_guard.allow_external_access)

    def is_room_tv_followed(self, code: str) -> bool:
        """Return True if the TV Dashboard is currently in Follow mode for `code`."""
        return self.tv_state.mode == TV_MODE_FOLLOW and self.tv_state.room_code == code

    def apply_options(self, options: dict[str, Any]) -> None:
        """Apply ConfigEntry / OptionsFlow settings dynamically without restart."""
        http_comp = getattr(self.hass, "http", None)
        trusted_proxies = getattr(http_comp, "trusted_proxies", []) or []
        use_xff = getattr(http_comp, "use_x_forwarded_for", True)

        self.network_guard.update(
            allow_external_access=options.get(
                CONF_ALLOW_EXTERNAL_ACCESS, DEFAULT_ALLOW_EXTERNAL_ACCESS
            ),
            allowed_subnets=options.get(CONF_ALLOWED_SUBNETS, DEFAULT_ALLOWED_SUBNETS),
            trusted_proxies=trusted_proxies,
            use_x_forwarded_for=use_xff,
        )
        raw_url = str(options.get(CONF_CUSTOM_LAN_BASE_URL, DEFAULT_CUSTOM_LAN_BASE_URL) or "").strip()
        self.custom_lan_base_url = raw_url.rstrip("/")

        if CONF_STAGE_EFFECTS in options:
            self.stage_director.update_config(options.get(CONF_STAGE_EFFECTS) or {})

        # Dynamic security revocation: if External Access is enabled at runtime, immediately
        # cancel countdown warnings & stage pulses and restore any snapshotted lights.
        if not self.is_stage_events_allowed():
            for task in list(self._warning_timers.values()):
                if not task.done():
                    task.cancel()
            self._warning_timers.clear()
            self.stage_director.cancel_active_effects()
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                self.stage_director.schedule_restore_lights(0.0)

    async def async_update_stage_config(self, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Validate, apply, and persist Smart-Home Stage Effects settings from Admin Panel."""
        if not isinstance(raw_config, dict):
            raise StageEffectError("invalid_config", "Stage settings payload must be a JSON object.")

        if not self.is_stage_events_allowed() and bool(raw_config.get("enabled", False)):
            raise StageEffectError(
                "stage_effects_disabled_external_mode",
                "Smart-Home Stage Effects cannot be enabled while External Access is active.",
                status_code=409,
            )

        updated = self.stage_director.update_config(raw_config)
        if not updated["enabled"]:
            self.stage_director.cancel_active_effects()
            await self.stage_director.async_restore_lights()

        # Persist inside ConfigEntry options so settings survive HA restarts
        if self._entry is not None:
            config_entries = getattr(self.hass, "config_entries", None)
            updater = getattr(config_entries, "async_update_entry", None) if config_entries else None
            if callable(updater):
                new_options = dict(getattr(self._entry, "options", {}) or {})
                new_options[CONF_STAGE_EFFECTS] = dict(updated)
                updater(self._entry, options=new_options)

        return updated

    async def async_run_stage_test(self, effect: str, *, language: str | None = None) -> dict[str, Any]:
        """Run an admin test cue for lights/speaker (strictly blocked in External Access mode)."""
        if not self.is_stage_events_allowed():
            raise StageEffectError(
                "stage_effects_disabled_external_mode",
                "Smart-Home Stage Effects & HA Events are disabled while External Access is enabled.",
                status_code=409,
            )
        return await self.stage_director.async_run_test_effect(effect, language=language)

    def emit_phase_changed(
        self,
        room: Any,
        *,
        sub_event: str,
        previous_phase: str | None = None,
        remaining_seconds: float | None = None,
    ) -> bool:
        """Fire `quizrooms_phase_changed` on HA Event Bus & drive StageDirector (LAN-Only)."""
        if not self.is_stage_events_allowed():
            return False

        payload = build_phase_changed_payload(
            self.engine,
            room,
            sub_event=sub_event,
            previous_phase=previous_phase,
            is_tv_followed=self.is_room_tv_followed(room.code),
            remaining_seconds=remaining_seconds,
        )
        bus = getattr(self.hass, "bus", None)
        if bus is not None and hasattr(bus, "async_fire"):
            try:
                bus.async_fire(EVENT_PHASE_CHANGED, payload)
            except Exception:
                _LOGGER.exception("Failed firing %s on HA Event Bus", EVENT_PHASE_CHANGED)

        self.stage_director.handle_phase_event(payload)
        return True

    def emit_finale(self, room: Any) -> bool:
        """Fire `quizrooms_finale` on HA Event Bus & drive StageDirector (LAN-Only)."""
        if not self.is_stage_events_allowed():
            return False

        payload = build_finale_payload(
            self.engine,
            room,
            is_tv_followed=self.is_room_tv_followed(room.code),
        )
        bus = getattr(self.hass, "bus", None)
        if bus is not None and hasattr(bus, "async_fire"):
            try:
                bus.async_fire(EVENT_FINALE, payload)
            except Exception:
                _LOGGER.exception("Failed firing %s on HA Event Bus", EVENT_FINALE)

        self.stage_director.handle_finale_event(payload)
        return True

    def resolve_lan_base_url(self, request: Any | None = None) -> str:
        """Determine the best LAN base URL for QR codes (hardened against Host header injection)."""
        if self.custom_lan_base_url and is_safe_http_url(self.custom_lan_base_url):
            return self.custom_lan_base_url

        ha_config = getattr(self.hass, "config", None)
        internal_url = getattr(ha_config, "internal_url", None) if ha_config else None
        if internal_url:
            clean_int = str(internal_url).strip().rstrip("/")
            if is_safe_http_url(clean_int):
                return clean_int

        if request is not None:
            scheme = str(getattr(request, "scheme", "http") or "http").strip().lower()
            host = str(getattr(request, "host", "") or "").strip()
            if scheme in ("http", "https") and host and len(host) <= 255 and _SAFE_HOST_RE.fullmatch(host):
                candidate = f"{scheme}://{host}".rstrip("/")
                if is_safe_http_url(candidate):
                    return candidate

        external_url = getattr(ha_config, "external_url", None) if ha_config else None
        if external_url:
            clean_ext = str(external_url).strip().rstrip("/")
            if is_safe_http_url(clean_ext):
                return clean_ext
        return ""

    # --- Entity listener management ---

    def add_entity_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._entity_listeners.append(callback)

        def _remove() -> None:
            if callback in self._entity_listeners:
                self._entity_listeners.remove(callback)

        return _remove

    def notify_entities(self) -> None:
        for cb in list(self._entity_listeners):
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error notifying QuizRooms entity listener")

    # --- Background lifecycle tasks ---

    def start_background_tasks(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def async_shutdown(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        for task in list(self._room_timers.values()):
            task.cancel()
        self._room_timers.clear()

        for task in list(self._warning_timers.values()):
            task.cancel()
        self._warning_timers.clear()

        await self.stage_director.async_shutdown()

        for client in list(self._ws_clients):
            try:
                await client.ws.close()
            except Exception:
                pass
        self._ws_clients.clear()
        self.pack_registry.prune_pinned_media(set())
        self.avatar_store.clear()

    def prune_unused_pinned_media(self) -> int:
        """Evict cached pinned media bytes not referenced by any active room."""
        active_ids: set[str] = set()
        for room in self.engine.rooms.values():
            active_ids.update(room.pinned_media.keys())
        return self.pack_registry.prune_pinned_media(active_ids)

    async def _cleanup_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
                expired = self.engine.cleanup_expired_rooms()
                if expired:
                    _LOGGER.info("Auto-expired %d inactive QuizRooms room(s): %s", len(expired), expired)
                    for code in expired:
                        self._cancel_room_timer(code)
                        if self.tv_state.room_code == code:
                            self.tv_state.mode = TV_MODE_OVERVIEW
                            self.tv_state.room_code = None
                            self.stage_director.cancel_active_effects()
                            self.stage_director.schedule_restore_lights(0.0)
                        await self.broadcast_room_closed(code, reason="Room expired due to inactivity.")
                    self.prune_unused_pinned_media()
                    await self.broadcast_lobby_and_tv()
                    self.notify_entities()
        except asyncio.CancelledError:
            return

    # --- Per-room question timer management ---

    def _cancel_room_timer(self, code: str) -> None:
        task = self._room_timers.pop(code, None)
        if task is not None and not task.done():
            task.cancel()
        warn_task = self._warning_timers.pop(code, None)
        if warn_task is not None and not warn_task.done():
            warn_task.cancel()

    def schedule_room_timer(self, code: str) -> None:
        """Schedule automatic reveal and T-5s countdown warning for the active question."""
        self._cancel_room_timer(code)
        room = self.engine.get_room(code)
        if room is None or room.phase != PHASE_QUESTION or room.paused or room.question_ends_at is None:
            return

        delay = max(0.05, room.question_ends_at - self.engine._now())
        self._room_timers[code] = asyncio.create_task(self._timer_worker(code, delay))

        # Schedule T-minus 5s warning event (LAN-only) if more than 5s remain & not already fired
        if (
            self.is_stage_events_allowed()
            and room.countdown_warning_fired_for_index != room.current_index
            and delay > (COUNTDOWN_WARNING_SECONDS + 0.05)
        ):
            warn_delay = delay - COUNTDOWN_WARNING_SECONDS
            self._warning_timers[code] = asyncio.create_task(
                self._countdown_warning_worker(code, room.current_index, warn_delay)
            )

    async def _countdown_warning_worker(
        self, code: str, expected_index: int, warn_delay: float
    ) -> None:
        try:
            await asyncio.sleep(warn_delay)
            room = self.engine.get_room(code)
            if (
                room is None
                or room.phase != PHASE_QUESTION
                or room.paused
                or room.current_index != expected_index
                or room.countdown_warning_fired_for_index == expected_index
            ):
                return
            room.countdown_warning_fired_for_index = expected_index
            self.emit_phase_changed(
                room,
                sub_event="final_countdown",
                previous_phase=PHASE_QUESTION,
                remaining_seconds=float(COUNTDOWN_WARNING_SECONDS),
            )
        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.exception("Error in QuizRooms countdown warning worker for room %s", code)
        finally:
            if self._warning_timers.get(code) is asyncio.current_task():
                self._warning_timers.pop(code, None)

    def schedule_auto_reveal(self, code: str, expected_index: int, delay: float = 1.1) -> None:
        """Schedule a tracked short beat before auto-revealing when all players lock in."""
        self._cancel_room_timer(code)
        self._room_timers[code] = asyncio.create_task(
            self._auto_reveal_worker(code, expected_index, delay)
        )

    async def _auto_reveal_worker(self, code: str, expected_index: int, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            room = self.engine.get_room(code)
            if (
                room is None
                or room.phase != PHASE_QUESTION
                or room.paused
                or room.current_index != expected_index
            ):
                return
            warn_task = self._warning_timers.pop(code, None)
            if warn_task is not None and not warn_task.done():
                warn_task.cancel()
            room, _summary = self.engine.reveal_current_question(code)
            await self.broadcast_room_state(code)
            await self.broadcast_lobby_and_tv()
            self.notify_entities()
            self.emit_phase_changed(room, sub_event="round_reveal", previous_phase=PHASE_QUESTION)
        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.exception("Error in QuizRooms auto-reveal worker for room %s", code)
        finally:
            if self._room_timers.get(code) is asyncio.current_task():
                self._room_timers.pop(code, None)

    async def _timer_worker(self, code: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            room = self.engine.get_room(code)
            if room is None or room.phase != PHASE_QUESTION or room.paused:
                return
            warn_task = self._warning_timers.pop(code, None)
            if warn_task is not None and not warn_task.done():
                warn_task.cancel()
            room, _summary = self.engine.reveal_current_question(code)
            await self.broadcast_room_state(code)
            await self.broadcast_lobby_and_tv()
            self.notify_entities()
            self.emit_phase_changed(room, sub_event="round_reveal", previous_phase=PHASE_QUESTION)
        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.exception("Error in QuizRooms timer worker for room %s", code)
        finally:
            if self._room_timers.get(code) is asyncio.current_task():
                self._room_timers.pop(code, None)

    # --- WebSocket registration & broadcasting ---

    def can_accept_ws(self, client_ip: str | None = None) -> bool:
        """Return True if global and per-IP WebSocket connection caps allow a new socket."""
        if len(self._ws_clients) >= MAX_WS_CLIENTS:
            return False
        if client_ip:
            same_ip = sum(1 for c in self._ws_clients if c.client_ip == client_ip)
            if same_ip >= MAX_WS_CLIENTS_PER_IP:
                return False
        return True

    def register_ws(self, client: WSClient) -> None:
        self._ws_clients.add(client)

    async def unregister_ws(self, client: WSClient) -> None:
        self._ws_clients.discard(client)
        if client.role == "room" and client.room_code and client.session_token:
            # Only mark disconnected if no other active socket holds this session in this room
            still_connected = any(
                c.role == "room"
                and c.room_code == client.room_code
                and c.session_token == client.session_token
                for c in self._ws_clients
            )
            if not still_connected:
                room = self.engine.set_player_connected(
                    client.room_code, client.session_token, False
                )
                if room is not None:
                    await self.broadcast_room_state(client.room_code)
                    await self.broadcast_lobby_and_tv()
                    self.notify_entities()

    async def _safe_send(self, client: WSClient, payload: dict[str, Any]) -> None:
        try:
            await asyncio.wait_for(client.ws.send_json(payload), timeout=WS_SEND_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._ws_clients.discard(client)
            close_fn = getattr(client.ws, "close", None)
            if callable(close_fn):
                try:
                    res = close_fn()
                    if asyncio.iscoroutine(res):
                        asyncio.create_task(res)
                except Exception:
                    pass

    async def broadcast_room_state(self, code: str) -> None:
        """Push tailored room_state messages to players in room and TVs following it."""
        room = self.engine.get_room(code)
        if room is None:
            return

        tasks = []
        tv_state_payload = {
            "type": "room_state",
            "room": self.engine.serialize_room_state(room, for_session_token=None),
        }
        for client in list(self._ws_clients):
            if client.role == "room" and client.room_code == code:
                payload = {
                    "type": "room_state",
                    "room": self.engine.serialize_room_state(
                        room, for_session_token=client.session_token
                    ),
                }
                tasks.append(self._safe_send(client, payload))
            elif (
                client.role == "tv"
                and self.tv_state.mode == TV_MODE_FOLLOW
                and self.tv_state.room_code == code
            ):
                tasks.append(self._safe_send(client, tv_state_payload))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def broadcast_room_closed(self, code: str, *, reason: str = "Room closed.") -> None:
        """Notify all clients inside or following a closed room."""
        payload = {"type": "room_closed", "code": code, "reason": reason}
        tasks = []
        for client in list(self._ws_clients):
            if (client.role == "room" and client.room_code == code) or (
                client.role == "tv" and self.tv_state.room_code == code
            ):
                tasks.append(self._safe_send(client, payload))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def broadcast_lobby_and_tv(self, *, include_packs: bool = False) -> None:
        """Broadcast open public rooms, capacity, and live global avatar availability.

        To keep Smart TV browsers snappy, omits the ~25 KB pack catalog on routine room
        updates (sending it only when packs are imported, deleted, or reloaded) and skips
        pushing `lobby_update` to TV clients currently in Follow mode.
        """
        lobby_payload: dict[str, Any] = {
            "type": "lobby_update",
            "lobby": self.engine.serialize_public_lobby(),
            "avatars": list_preset_catalog(self.engine.get_taken_preset_avatars()),
        }
        if include_packs:
            lobby_payload["packs"] = [p.to_summary() for p in self.pack_registry.list_packs()]

        tv_in_follow = self.tv_state.mode == TV_MODE_FOLLOW and bool(self.tv_state.room_code)
        tasks = [
            self._safe_send(client, lobby_payload)
            for client in list(self._ws_clients)
            if client.role == "lobby" or (client.role == "tv" and not tv_in_follow)
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def broadcast_tv_mode(self) -> None:
        """Push TV mode/room/theme changes to all connected TV displays."""
        followed_room = (
            self.engine.get_room(self.tv_state.room_code)
            if self.tv_state.mode == TV_MODE_FOLLOW and self.tv_state.room_code
            else None
        )
        payload = {
            "type": "tv_mode_changed",
            "tv": self.tv_state.to_dict(),
            "room": (
                self.engine.serialize_room_state(followed_room, for_session_token=None)
                if followed_room is not None
                else None
            ),
            "lobby": self.engine.serialize_public_lobby(),
        }
        tasks = [
            self._safe_send(client, payload)
            for client in list(self._ws_clients)
            if client.role == "tv"
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # --- High-level coordinated actions (called by WS, Admin API, and HA services) ---

    async def async_set_max_rooms(self, value: int) -> int:
        new_val = self.engine.set_max_rooms(value)
        await self.broadcast_lobby_and_tv()
        self.notify_entities()
        return new_val

    async def async_set_tv_mode(
        self,
        *,
        mode: str,
        room_code: str | None = None,
        theme: str | None = None,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        clean_mode = (mode or TV_MODE_OVERVIEW).strip().lower()
        if clean_mode not in ALLOWED_TV_MODES:
            raise EngineError("invalid_tv_mode", f"TV mode must be one of {ALLOWED_TV_MODES}.")

        target_code: str | None = None
        if clean_mode == TV_MODE_FOLLOW:
            if not room_code:
                raise EngineError("missing_room_code", "room_code is required for Follow mode.")
            room = self.engine.get_room(room_code)
            if room is None:
                raise EngineError("room_not_found", f"Room '{room_code}' was not found.", status_code=404)
            if not is_admin and room.settings.visibility != VISIBILITY_PUBLIC:
                raise EngineError(
                    "forbidden",
                    "Private rooms can only be followed on TV by a Home Assistant administrator.",
                    status_code=403,
                )
            target_code = room.code

        if theme is not None and theme != "":
            clean_theme = theme.strip().lower()
            if clean_theme not in ALLOWED_THEMES:
                raise EngineError("invalid_theme", f"Theme must be one of {ALLOWED_THEMES}.")
            self.tv_state.theme = clean_theme

        prev_mode = self.tv_state.mode
        prev_code = self.tv_state.room_code

        self.tv_state.mode = clean_mode
        self.tv_state.room_code = target_code
        if prev_mode == TV_MODE_FOLLOW and (clean_mode != TV_MODE_FOLLOW or target_code != prev_code):
            self.stage_director.cancel_active_effects()
            self.stage_director.schedule_restore_lights(0.0)
        await self.broadcast_tv_mode()
        return self.tv_state.to_dict()

    async def async_close_room(
        self,
        code: str,
        *,
        host_token: str | None = None,
        is_admin: bool = False,
        reason: str = "Room was closed.",
    ) -> None:
        norm_code = (code or "").strip().upper()
        was_followed = self.tv_state.room_code == norm_code
        self.engine.close_room(norm_code, host_token=host_token, is_admin=is_admin)
        self._cancel_room_timer(norm_code)
        self.prune_unused_pinned_media()
        if was_followed:
            self.tv_state.mode = TV_MODE_OVERVIEW
            self.tv_state.room_code = None
            self.stage_director.cancel_active_effects()
            self.stage_director.schedule_restore_lights(0.0)
            await self.broadcast_tv_mode()
        await self.broadcast_room_closed(norm_code, reason=reason)
        await self.broadcast_lobby_and_tv()
        self.notify_entities()

    async def async_pause_room(
        self,
        code: str,
        *,
        paused: bool | None = None,
        host_token: str | None = None,
        is_admin: bool = False,
    ) -> None:
        room = self.engine.pause_room(
            code, paused=paused, host_token=host_token, is_admin=is_admin
        )
        if room.paused:
            self._cancel_room_timer(room.code)
        elif room.phase == PHASE_QUESTION:
            self.schedule_room_timer(room.code)
        await self.broadcast_room_state(room.code)
        await self.broadcast_lobby_and_tv()
        self.notify_entities()
        self.emit_phase_changed(
            room,
            sub_event="paused" if room.paused else "resumed",
            previous_phase=room.phase,
        )

    async def async_start_game(
        self,
        code: str,
        *,
        host_token: str | None = None,
        is_admin: bool = False,
    ) -> Any:
        """Start a room and pin snapshot media bytes off the event loop."""
        room = self.engine.start_game(
            code, host_token=host_token, is_admin=is_admin, pin_media=False
        )
        needed_media = {q.media_id for q in room.questions if q.media_id}
        if needed_media:
            pinned = await self.async_run_in_executor(
                self.pack_registry.pin_media_for_room, needed_media
            )
            # Room could have been closed by an admin while pinning media off-thread
            if self.engine.get_room(room.code) is not room:
                self.prune_unused_pinned_media()
                return room
            room.pinned_media = pinned
        self.schedule_room_timer(room.code)
        await self.broadcast_room_state(room.code)
        await self.broadcast_lobby_and_tv()
        self.notify_entities()
        self.emit_phase_changed(room, sub_event="round_start", previous_phase=PHASE_LOBBY)
        return room

    async def async_next_step(
        self,
        code: str,
        *,
        host_token: str | None = None,
        is_admin: bool = False,
    ) -> Any:
        """Advance Question -> Reveal, or Reveal -> Next Question / Finale, and emit HA events."""
        existing = self.engine.get_room(code)
        prev_phase = existing.phase if existing is not None else None
        room = self.engine.next_step(code, host_token=host_token, is_admin=is_admin)
        if room.phase == PHASE_QUESTION:
            self.schedule_room_timer(room.code)
        else:
            self._cancel_room_timer(room.code)
        await self.broadcast_room_state(room.code)
        await self.broadcast_lobby_and_tv()
        self.notify_entities()

        if room.phase == PHASE_REVEAL:
            self.emit_phase_changed(room, sub_event="round_reveal", previous_phase=prev_phase)
        elif room.phase == PHASE_QUESTION:
            self.emit_phase_changed(room, sub_event="round_start", previous_phase=prev_phase)
        elif room.phase == PHASE_FINALE:
            self.emit_finale(room)
        return room

    async def async_play_again(
        self,
        code: str,
        *,
        host_token: str | None = None,
        is_admin: bool = False,
    ) -> Any:
        """Reset a finished room back to Lobby, cancel timers, and notify HA Event Bus."""
        existing = self.engine.get_room(code)
        prev_phase = existing.phase if existing is not None else None
        room = self.engine.play_again(code, host_token=host_token, is_admin=is_admin)
        self._cancel_room_timer(room.code)
        self.prune_unused_pinned_media()
        await self.broadcast_room_state(room.code)
        await self.broadcast_lobby_and_tv()
        self.notify_entities()
        self.emit_phase_changed(room, sub_event="play_again", previous_phase=prev_phase)
        return room

    async def async_reload_packs(self) -> list[dict[str, Any]]:
        packs = await self.async_run_in_executor(self.pack_registry.reload)
        self.prune_unused_pinned_media()
        await self.broadcast_lobby_and_tv(include_packs=True)
        return [p.to_summary() for p in packs]
