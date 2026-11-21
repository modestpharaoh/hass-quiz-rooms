"""Runtime coordinator connecting QuizEngine, WebSocket hub, timers, and HA entities."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any, Callable

from .const import (
    ALLOWED_THEMES,
    ALLOWED_TV_MODES,
    CLEANUP_INTERVAL_SECONDS,
    CONF_ALLOW_EXTERNAL_ACCESS,
    CONF_ALLOWED_SUBNETS,
    CONF_CUSTOM_LAN_BASE_URL,
    CUSTOM_PACKS_REL_DIR,
    DEFAULT_ALLOW_EXTERNAL_ACCESS,
    DEFAULT_ALLOWED_SUBNETS,
    DEFAULT_CUSTOM_LAN_BASE_URL,
    DEFAULT_MAX_ROOMS,
    PHASE_QUESTION,
    THEME_AUTO,
    TV_MODE_FOLLOW,
    TV_MODE_OVERVIEW,
)
from .engine import EngineError, QuizEngine
from .network_guard import NetworkGuard
from .packs import PackRegistry

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
    ) -> None:
        self.hass = hass
        self.frontend_dir = Path(frontend_dir)
        self.pack_registry = PackRegistry(builtin_packs_dir, custom_packs_dir)
        if load_packs:
            self.frontend_dir = self.frontend_dir.resolve()
            self.pack_registry.reload()

        self.engine = QuizEngine(self.pack_registry, max_rooms=DEFAULT_MAX_ROOMS)
        self.network_guard = NetworkGuard()
        self.custom_lan_base_url: str = DEFAULT_CUSTOM_LAN_BASE_URL
        self.tv_state = TVState()

        self._ws_clients: set[WSClient] = set()
        self._room_timers: dict[str, asyncio.Task[None]] = {}
        self._cleanup_task: asyncio.Task[None] | None = None
        self._entity_listeners: list[Callable[[], None]] = []

        self.apply_options(options or {})

    @classmethod
    def from_hass(cls, hass: Any, options: dict[str, Any] | None = None) -> "QuizRoomsCoordinator":
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
            self.pack_registry.reload()

        await self.async_run_in_executor(_init_sync)

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

    def resolve_lan_base_url(self, request: Any | None = None) -> str:
        """Determine the best LAN base URL for QR codes."""
        if self.custom_lan_base_url:
            return self.custom_lan_base_url

        ha_config = getattr(self.hass, "config", None)
        internal_url = getattr(ha_config, "internal_url", None) if ha_config else None
        if internal_url:
            return str(internal_url).rstrip("/")

        if request is not None:
            scheme = getattr(request, "scheme", "http")
            host = getattr(request, "host", "")
            if host:
                return f"{scheme}://{host}".rstrip("/")

        external_url = getattr(ha_config, "external_url", None) if ha_config else None
        if external_url:
            return str(external_url).rstrip("/")
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

        for client in list(self._ws_clients):
            try:
                await client.ws.close()
            except Exception:
                pass
        self._ws_clients.clear()

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
                        await self.broadcast_room_closed(code, reason="Room expired due to inactivity.")
                    await self.broadcast_lobby_and_tv()
                    self.notify_entities()
        except asyncio.CancelledError:
            return

    # --- Per-room question timer management ---

    def _cancel_room_timer(self, code: str) -> None:
        task = self._room_timers.pop(code, None)
        if task is not None and not task.done():
            task.cancel()

    def schedule_room_timer(self, code: str) -> None:
        """Schedule automatic reveal when the active room's question timer expires."""
        self._cancel_room_timer(code)
        room = self.engine.get_room(code)
        if room is None or room.phase != PHASE_QUESTION or room.paused or room.question_ends_at is None:
            return

        delay = max(0.05, room.question_ends_at - self.engine._now())
        self._room_timers[code] = asyncio.create_task(self._timer_worker(code, delay))

    async def _timer_worker(self, code: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            room = self.engine.get_room(code)
            if room is None or room.phase != PHASE_QUESTION or room.paused:
                return
            self.engine.reveal_current_question(code)
            await self.broadcast_room_state(code)
            await self.broadcast_lobby_and_tv()
            self.notify_entities()
        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.exception("Error in QuizRooms timer worker for room %s", code)

    # --- WebSocket registration & broadcasting ---

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
            await client.ws.send_json(payload)
        except Exception:
            self._ws_clients.discard(client)

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

    async def broadcast_lobby_and_tv(self) -> None:
        """Broadcast open public rooms and room capacity to lobby & TV clients."""
        lobby_payload = {
            "type": "lobby_update",
            "lobby": self.engine.serialize_public_lobby(),
            "packs": [p.to_summary() for p in self.pack_registry.list_packs()],
        }
        tasks = [
            self._safe_send(client, lobby_payload)
            for client in list(self._ws_clients)
            if client.role in ("lobby", "tv")
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
            target_code = room.code

        if theme is not None and theme != "":
            clean_theme = theme.strip().lower()
            if clean_theme not in ALLOWED_THEMES:
                raise EngineError("invalid_theme", f"Theme must be one of {ALLOWED_THEMES}.")
            self.tv_state.theme = clean_theme

        self.tv_state.mode = clean_mode
        self.tv_state.room_code = target_code
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
        self.engine.close_room(norm_code, host_token=host_token, is_admin=is_admin)
        self._cancel_room_timer(norm_code)
        if self.tv_state.room_code == norm_code:
            self.tv_state.mode = TV_MODE_OVERVIEW
            self.tv_state.room_code = None
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
            room.pinned_media = await self.async_run_in_executor(
                self.pack_registry.pin_media_for_room, needed_media
            )
        self.schedule_room_timer(room.code)
        await self.broadcast_room_state(room.code)
        await self.broadcast_lobby_and_tv()
        self.notify_entities()
        return room

    async def async_reload_packs(self) -> list[dict[str, Any]]:
        packs = await self.async_run_in_executor(self.pack_registry.reload)
        await self.broadcast_lobby_and_tv()
        return [p.to_summary() for p in packs]
