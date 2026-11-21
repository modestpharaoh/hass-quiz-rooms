"""HTTP views and WebSocket handler for QuizRooms (Guest, TV, Media, and Admin APIs)."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from homeassistant.components.http import HomeAssistantView

from .const import (
    DOMAIN,
    MAX_UPLOAD_BYTES,
    PHASE_QUESTION,
    URL_ADMIN_PANEL_JS,
    URL_API_ADMIN,
    URL_API_PUBLIC,
    URL_ASSETS,
    URL_MEDIA,
    URL_PLAY,
    URL_TV,
    URL_WS,
)
from .coordinator import QuizRoomsCoordinator, WSClient
from .engine import EngineError
from .network_guard import build_forbidden_response
from .packs import PackValidationError, sample_pack_template

_LOGGER = logging.getLogger(__name__)

# Strict security headers for uploaded/bundled pack media (prevents SVG XSS execution)
MEDIA_SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, max-age=3600",
}

ASSET_MIME_OVERRIDES: dict[str, str] = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".json": "application/json; charset=utf-8",
}


def _check_network_guard(coordinator: QuizRoomsCoordinator, request: web.Request) -> web.Response | None:
    """Enforce NetworkGuard directly on unauthenticated views as defense-in-depth."""
    decision = coordinator.network_guard.evaluate_request(request)
    if not decision.allowed:
        return build_forbidden_response(request, decision)
    return None


def _require_ha_admin(request: web.Request) -> web.Response | None:
    """Verify request comes from an authenticated Home Assistant administrator."""
    user = request.get("hass_user")
    if user is None or not getattr(user, "is_admin", False):
        return web.json_response(
            {"error": "admin_required", "message": "Home Assistant administrator privileges required."},
            status=403,
        )
    return None


def _read_text_sync(path: Path) -> str:
    """Read UTF-8 text file synchronously (always invoke via executor)."""
    return path.read_text(encoding="utf-8")


def _read_asset_sync(frontend_dir: Path, filepath: str) -> tuple[bytes, str] | None:
    """Resolve and read a local frontend asset synchronously (always invoke via executor)."""
    if not filepath or "\x00" in filepath or "\\" in filepath:
        return None
    assets_root = (frontend_dir / "assets").resolve()
    candidate = (assets_root / filepath).resolve()
    if not candidate.is_relative_to(assets_root) or not candidate.is_file():
        return None
    ext = candidate.suffix.lower()
    content_type = (
        ASSET_MIME_OVERRIDES.get(ext)
        or mimetypes.guess_type(str(candidate))[0]
        or "application/octet-stream"
    )
    return candidate.read_bytes(), content_type


class _QuizRoomsBaseView(HomeAssistantView):
    """Base view that resolves the active QuizRoomsCoordinator cleanly across reloads."""

    def __init__(self, coordinator: QuizRoomsCoordinator) -> None:
        self._fallback_coordinator = coordinator

    @property
    def coordinator(self) -> QuizRoomsCoordinator:
        hass = getattr(self._fallback_coordinator, "hass", None)
        hass_data = getattr(hass, "data", None)
        if isinstance(hass_data, dict):
            live = hass_data.get(DOMAIN, {}).get("coordinator")
            if live is not None:
                return live
        return self._fallback_coordinator


# ---------------------------------------------------------------------------
# Unauthenticated LAN-Guarded Guest, TV, Asset & Media Views
# ---------------------------------------------------------------------------


class QuizRoomsPlayView(_QuizRoomsBaseView):
    """Serve the Guest Lobby & Player SPA at /quizrooms/play."""

    url = URL_PLAY
    name = "quizrooms:play"
    requires_auth = False

    async def get(self, request: web.Request) -> web.StreamResponse:
        if blocked := _check_network_guard(self.coordinator, request):
            return blocked
        html_path = self.coordinator.frontend_dir / "play.html"
        html = await self.coordinator.async_run_in_executor(_read_text_sync, html_path)
        return web.Response(
            text=html,
            content_type="text/html",
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )


class QuizRoomsTVView(_QuizRoomsBaseView):
    """Serve the 16:9 TV Studio Dashboard at /quizrooms/tv."""

    url = URL_TV
    name = "quizrooms:tv"
    requires_auth = False

    async def get(self, request: web.Request) -> web.StreamResponse:
        if blocked := _check_network_guard(self.coordinator, request):
            return blocked
        html_path = self.coordinator.frontend_dir / "tv.html"
        html = await self.coordinator.async_run_in_executor(_read_text_sync, html_path)
        return web.Response(
            text=html,
            content_type="text/html",
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )


class QuizRoomsAssetsView(_QuizRoomsBaseView):
    """Serve local CSS, JS, and bundled fonts under /quizrooms/assets/{filepath:.+}."""

    url = f"{URL_ASSETS}/{{filepath:.+}}"
    name = "quizrooms:assets"
    requires_auth = False

    async def get(self, request: web.Request, filepath: str) -> web.StreamResponse:
        if blocked := _check_network_guard(self.coordinator, request):
            return blocked

        found = await self.coordinator.async_run_in_executor(
            _read_asset_sync, self.coordinator.frontend_dir, filepath
        )
        if found is None:
            return web.Response(status=404)

        data, content_type = found
        return web.Response(
            body=data,
            headers={
                "Content-Type": content_type,
                "Cache-Control": "public, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )


class QuizRoomsMediaView(_QuizRoomsBaseView):
    """Serve pack images via opaque IDs at /quizrooms/media/{opaque_id} with strict sandbox CSP."""

    url = f"{URL_MEDIA}/{{opaque_id}}"
    name = "quizrooms:media"
    requires_auth = False

    async def get(self, request: web.Request, opaque_id: str) -> web.StreamResponse:
        if blocked := _check_network_guard(self.coordinator, request):
            return blocked

        found = await self.coordinator.async_run_in_executor(
            self.coordinator.pack_registry.get_media_bytes, opaque_id
        )
        if found is None:
            return web.Response(
                status=404,
                text="Media not found",
                headers=dict(MEDIA_SECURITY_HEADERS),
            )

        data, content_type = found
        headers = dict(MEDIA_SECURITY_HEADERS)
        headers["Content-Type"] = content_type
        return web.Response(body=data, headers=headers)


class QuizRoomsPublicBootstrapView(_QuizRoomsBaseView):
    """Return initial lobby state, packs, and LAN base URL for /play and /tv."""

    url = f"{URL_API_PUBLIC}/bootstrap"
    name = "quizrooms:public_bootstrap"
    requires_auth = False

    async def get(self, request: web.Request) -> web.StreamResponse:
        if blocked := _check_network_guard(self.coordinator, request):
            return blocked

        return web.json_response(
            {
                "lobby": self.coordinator.engine.serialize_public_lobby(),
                "packs": [p.to_summary() for p in self.coordinator.pack_registry.list_packs()],
                "tv": self.coordinator.tv_state.to_dict(),
                "lan_base_url": self.coordinator.resolve_lan_base_url(request),
            }
        )


class QuizRoomsWebSocketView(_QuizRoomsBaseView):
    """Multiplexed WebSocket endpoint at /quizrooms/ws for Lobby, Players, and TV displays."""

    url = URL_WS
    name = "quizrooms:ws"
    requires_auth = False

    async def get(self, request: web.Request) -> web.StreamResponse:
        if blocked := _check_network_guard(self.coordinator, request):
            return blocked

        ws = web.WebSocketResponse(heartbeat=25.0)
        await ws.prepare(request)

        role = request.query.get("role", "lobby").strip().lower()
        if role not in ("lobby", "room", "tv"):
            role = "lobby"

        client = WSClient(ws=ws, role=role)
        self.coordinator.register_ws(client)

        try:
            # Push initial snapshot on connect
            await ws.send_json(
                {
                    "type": "hello",
                    "role": role,
                    "lan_base_url": self.coordinator.resolve_lan_base_url(request),
                    "lobby": self.coordinator.engine.serialize_public_lobby(),
                    "packs": [p.to_summary() for p in self.coordinator.pack_registry.list_packs()],
                    "tv": self.coordinator.tv_state.to_dict(),
                }
            )
            if role == "tv" and self.coordinator.tv_state.mode == "follow" and self.coordinator.tv_state.room_code:
                followed = self.coordinator.engine.get_room(self.coordinator.tv_state.room_code)
                if followed is not None:
                    await ws.send_json(
                        {
                            "type": "room_state",
                            "room": self.coordinator.engine.serialize_room_state(followed),
                        }
                    )

            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_message(client, msg.data)
                elif msg.type == WSMsgType.ERROR:
                    _LOGGER.debug("QuizRooms WebSocket error: %s", ws.exception())
                    break
        finally:
            await self.coordinator.unregister_ws(client)

        return ws

    async def _handle_message(self, client: WSClient, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await client.ws.send_json({"type": "error", "code": "invalid_json", "message": "Malformed JSON."})
            return

        if not isinstance(data, dict):
            return

        msg_type = str(data.get("type") or "").strip()
        engine = self.coordinator.engine

        try:
            if msg_type == "ping":
                await client.ws.send_json({"type": "pong"})
                return

            if msg_type == "subscribe_lobby":
                client.role = "lobby"
                client.room_code = None
                await client.ws.send_json(
                    {
                        "type": "lobby_update",
                        "lobby": engine.serialize_public_lobby(),
                        "packs": [p.to_summary() for p in self.coordinator.pack_registry.list_packs()],
                    }
                )
                return

            if msg_type == "create_room":
                room, session_token, host_token = engine.create_room(
                    host_session_token=data.get("session_token"),
                    host_nickname=str(data.get("nickname") or ""),
                    room_name=data.get("room_name"),
                    pack_ids=data.get("pack_ids"),
                    round_count=data.get("round_count"),
                    timer_seconds=data.get("timer_seconds"),
                    answer_mode=data.get("answer_mode"),
                    wager_mode=data.get("wager_mode"),
                    visibility=data.get("visibility"),
                    allow_late_join=data.get("allow_late_join"),
                )
                client.role = "room"
                client.room_code = room.code
                client.session_token = session_token
                await client.ws.send_json(
                    {
                        "type": "room_created",
                        "code": room.code,
                        "session_token": session_token,
                        "host_token": host_token,
                        "room": engine.serialize_room_state(room, for_session_token=session_token),
                    }
                )
                await self.coordinator.broadcast_lobby_and_tv()
                self.coordinator.notify_entities()
                return

            if msg_type == "join_room":
                room, player, session_token = engine.join_room(
                    str(data.get("code") or ""),
                    session_token=data.get("session_token"),
                    nickname=data.get("nickname"),
                )
                client.role = "room"
                client.room_code = room.code
                client.session_token = session_token
                await client.ws.send_json(
                    {
                        "type": "joined_room",
                        "code": room.code,
                        "session_token": session_token,
                        "is_host": player.is_host,
                        "room": engine.serialize_room_state(room, for_session_token=session_token),
                    }
                )
                await self.coordinator.broadcast_room_state(room.code)
                await self.coordinator.broadcast_lobby_and_tv()
                self.coordinator.notify_entities()
                return

            if msg_type == "leave_room":
                old_code = client.room_code
                old_token = client.session_token
                client.role = "lobby"
                client.room_code = None
                if old_code and old_token:
                    engine.set_player_connected(old_code, old_token, False)
                    await self.coordinator.broadcast_room_state(old_code)
                    await self.coordinator.broadcast_lobby_and_tv()
                    self.coordinator.notify_entities()
                await client.ws.send_json(
                    {
                        "type": "lobby_update",
                        "lobby": engine.serialize_public_lobby(),
                        "packs": [p.to_summary() for p in self.coordinator.pack_registry.list_packs()],
                    }
                )
                return

            if msg_type == "update_settings":
                code = str(data.get("code") or client.room_code or "")
                room = engine.update_room_settings(
                    code,
                    host_token=data.get("host_token"),
                    room_name=data.get("room_name"),
                    pack_ids=data.get("pack_ids"),
                    round_count=data.get("round_count"),
                    timer_seconds=data.get("timer_seconds"),
                    answer_mode=data.get("answer_mode"),
                    wager_mode=data.get("wager_mode"),
                    visibility=data.get("visibility"),
                    allow_late_join=data.get("allow_late_join"),
                )
                await self.coordinator.broadcast_room_state(room.code)
                await self.coordinator.broadcast_lobby_and_tv()
                self.coordinator.notify_entities()
                return

            if msg_type == "start_game":
                code = str(data.get("code") or client.room_code or "")
                await self.coordinator.async_start_game(code, host_token=data.get("host_token"))
                return

            if msg_type == "submit_answer":
                code = str(data.get("code") or client.room_code or "")
                session_token = str(data.get("session_token") or client.session_token or "")
                room, _sub, all_locked = engine.submit_answer(
                    code,
                    session_token=session_token,
                    answer=str(data.get("answer") or ""),
                    wager=data.get("wager"),
                )
                await self.coordinator.broadcast_room_state(room.code)
                if all_locked:
                    # Short dramatic beat before auto-reveal when every connected player is locked in
                    self.coordinator._cancel_room_timer(room.code)
                    asyncio.create_task(self._auto_reveal_after_beat(room.code, room.current_index))
                return

            if msg_type == "next_step":
                code = str(data.get("code") or client.room_code or "")
                room = engine.next_step(code, host_token=data.get("host_token"))
                if room.phase == PHASE_QUESTION:
                    self.coordinator.schedule_room_timer(room.code)
                else:
                    self.coordinator._cancel_room_timer(room.code)
                await self.coordinator.broadcast_room_state(room.code)
                await self.coordinator.broadcast_lobby_and_tv()
                self.coordinator.notify_entities()
                return

            if msg_type == "override_answer":
                code = str(data.get("code") or client.room_code or "")
                room, _summary = engine.override_free_text_answer(
                    code,
                    target_session_token=str(data.get("target_session_token") or ""),
                    approve=bool(data.get("approve", True)),
                    host_token=data.get("host_token"),
                )
                await self.coordinator.broadcast_room_state(room.code)
                await self.coordinator.broadcast_lobby_and_tv()
                return

            if msg_type == "pause_room":
                code = str(data.get("code") or client.room_code or "")
                await self.coordinator.async_pause_room(
                    code,
                    paused=data.get("paused"),
                    host_token=data.get("host_token"),
                )
                return

            if msg_type == "play_again":
                code = str(data.get("code") or client.room_code or "")
                room = engine.play_again(code, host_token=data.get("host_token"))
                self.coordinator._cancel_room_timer(room.code)
                await self.coordinator.broadcast_room_state(room.code)
                await self.coordinator.broadcast_lobby_and_tv()
                self.coordinator.notify_entities()
                return

            if msg_type == "close_room":
                code = str(data.get("code") or client.room_code or "")
                await self.coordinator.async_close_room(
                    code,
                    host_token=data.get("host_token"),
                    reason="Room closed by host.",
                )
                return

            if msg_type == "tv_set_mode":
                # TV dashboard on-screen click / remote switcher
                await self.coordinator.async_set_tv_mode(
                    mode=str(data.get("mode") or "overview"),
                    room_code=data.get("room_code"),
                    theme=data.get("theme"),
                )
                return

            await client.ws.send_json(
                {"type": "error", "code": "unknown_message", "message": f"Unknown action '{msg_type}'."}
            )
        except EngineError as err:
            payload = err.to_dict()
            payload["type"] = "error"
            await client.ws.send_json(payload)
        except Exception:
            _LOGGER.exception("Unhandled error processing WS message %s", msg_type)
            await client.ws.send_json(
                {"type": "error", "code": "internal_error", "message": "Unexpected server error."}
            )

    async def _auto_reveal_after_beat(self, code: str, expected_index: int) -> None:
        await asyncio.sleep(1.1)
        room = self.coordinator.engine.get_room(code)
        if room is None or room.phase != PHASE_QUESTION or room.current_index != expected_index:
            return
        self.coordinator.engine.reveal_current_question(code)
        await self.coordinator.broadcast_room_state(code)
        await self.coordinator.broadcast_lobby_and_tv()
        self.coordinator.notify_entities()


# ---------------------------------------------------------------------------
# Authenticated Home Assistant Admin Panel & Admin APIs
# ---------------------------------------------------------------------------


class QuizRoomsAdminPanelJSView(_QuizRoomsBaseView):
    """Serve the HA Sidebar Admin Panel JS module."""

    url = URL_ADMIN_PANEL_JS
    name = "quizrooms:admin_panel_js"
    requires_auth = False  # Browser module import from HA sidebar; all data APIs require HA admin auth

    async def get(self, request: web.Request) -> web.StreamResponse:
        js_path = self.coordinator.frontend_dir / "admin-panel.js"
        js_text = await self.coordinator.async_run_in_executor(_read_text_sync, js_path)
        return web.Response(
            text=js_text,
            content_type="application/javascript",
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )


class QuizRoomsAdminStateView(_QuizRoomsBaseView):
    """Return full admin state (all rooms, packs, max_rooms, TV state)."""

    url = f"{URL_API_ADMIN}/state"
    name = "quizrooms:admin_state"
    requires_auth = True

    async def get(self, request: web.Request) -> web.StreamResponse:
        if denied := _require_ha_admin(request):
            return denied

        return web.json_response(
            {
                "rooms": self.coordinator.engine.serialize_admin_rooms(),
                "active_rooms": self.coordinator.engine.active_room_count,
                "active_players": self.coordinator.engine.active_player_count,
                "max_rooms": self.coordinator.engine.max_rooms,
                "packs": [p.to_summary() for p in self.coordinator.pack_registry.list_packs()],
                "tv": self.coordinator.tv_state.to_dict(),
                "lan_base_url": self.coordinator.resolve_lan_base_url(request),
                "allow_external_access": self.coordinator.network_guard.allow_external_access,
            }
        )


class QuizRoomsAdminPackTemplateView(HomeAssistantView):
    """Downloadable sample pack JSON template for the Admin UI."""

    url = f"{URL_API_ADMIN}/packs/template"
    name = "quizrooms:admin_pack_template"
    requires_auth = True

    async def get(self, request: web.Request) -> web.StreamResponse:
        if denied := _require_ha_admin(request):
            return denied
        payload = json.dumps(sample_pack_template(), ensure_ascii=False, indent=2)
        return web.Response(
            text=payload,
            content_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="quizrooms-sample-pack.json"',
                "X-Content-Type-Options": "nosniff",
            },
        )


class QuizRoomsAdminPackImportView(_QuizRoomsBaseView):
    """Upload and import a .json or .zip pack archive (Admin only)."""

    url = f"{URL_API_ADMIN}/packs/import"
    name = "quizrooms:admin_pack_import"
    requires_auth = True

    async def post(self, request: web.Request) -> web.StreamResponse:
        if denied := _require_ha_admin(request):
            return denied

        overwrite = request.query.get("overwrite", "").lower() in ("1", "true", "yes")
        filename = ""
        file_bytes = b""

        content_type = request.content_type or ""
        if content_type.startswith("multipart/"):
            reader = await request.multipart()
            while (part := await reader.next()) is not None:
                if part.name == "overwrite":
                    val = (await part.text()).strip().lower()
                    overwrite = val in ("1", "true", "yes")
                elif part.name == "file":
                    filename = part.filename or ""
                    chunks: list[bytes] = []
                    total = 0
                    while True:
                        chunk = await part.read_chunk(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_UPLOAD_BYTES:
                            return web.json_response(
                                {
                                    "error": "file_too_large",
                                    "message": f"Upload exceeds maximum size of {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                                    "details": [f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."],
                                },
                                status=413,
                            )
                        chunks.append(chunk)
                    file_bytes = b"".join(chunks)
        else:
            filename = request.headers.get("X-Pack-Filename", "pack.json")
            file_bytes = await request.read()

        try:
            pack = await self.coordinator.async_run_in_executor(
                lambda: self.coordinator.pack_registry.import_pack_bytes(
                    filename, file_bytes, overwrite=overwrite
                )
            )
        except PackValidationError as err:
            return web.json_response(
                {
                    "error": err.code,
                    "message": err.message,
                    "details": err.details,
                },
                status=err.status_code,
            )

        await self.coordinator.broadcast_lobby_and_tv()
        return web.json_response({"ok": True, "pack": pack.to_summary()}, status=201)


class QuizRoomsAdminPackDeleteView(_QuizRoomsBaseView):
    """Delete an imported custom pack (built-in packs are protected)."""

    url = f"{URL_API_ADMIN}/packs/{{pack_id}}"
    name = "quizrooms:admin_pack_delete"
    requires_auth = True

    async def delete(self, request: web.Request, pack_id: str) -> web.StreamResponse:
        if denied := _require_ha_admin(request):
            return denied

        try:
            await self.coordinator.async_run_in_executor(
                self.coordinator.pack_registry.delete_custom_pack, pack_id
            )
        except PackValidationError as err:
            return web.json_response(
                {"error": err.code, "message": err.message, "details": err.details},
                status=err.status_code,
            )

        await self.coordinator.broadcast_lobby_and_tv()
        return web.json_response({"ok": True, "deleted": pack_id})


class QuizRoomsAdminRoomActionView(_QuizRoomsBaseView):
    """Allow HA Admins to advance, pause, resume, or force-close any room."""

    url = f"{URL_API_ADMIN}/rooms/{{code}}/action"
    name = "quizrooms:admin_room_action"
    requires_auth = True

    async def post(self, request: web.Request, code: str) -> web.StreamResponse:
        if denied := _require_ha_admin(request):
            return denied

        try:
            body = await request.json()
        except Exception:
            body = {}

        action = str(body.get("action") or "").strip().lower()
        try:
            if action == "close":
                await self.coordinator.async_close_room(
                    code, is_admin=True, reason="Room closed by Home Assistant admin."
                )
            elif action == "pause":
                await self.coordinator.async_pause_room(code, paused=True, is_admin=True)
            elif action == "resume":
                await self.coordinator.async_pause_room(code, paused=False, is_admin=True)
            elif action == "next":
                room = self.coordinator.engine.next_step(code, is_admin=True)
                if room.phase == PHASE_QUESTION:
                    self.coordinator.schedule_room_timer(room.code)
                else:
                    self.coordinator._cancel_room_timer(room.code)
                await self.coordinator.broadcast_room_state(room.code)
                await self.coordinator.broadcast_lobby_and_tv()
                self.coordinator.notify_entities()
            elif action == "start":
                await self.coordinator.async_start_game(code, is_admin=True)
            else:
                return web.json_response(
                    {"error": "invalid_action", "message": f"Unsupported action '{action}'."},
                    status=400,
                )
        except EngineError as err:
            return web.json_response(err.to_dict(), status=err.status_code)

        return web.json_response({"ok": True})


class QuizRoomsAdminTVView(_QuizRoomsBaseView):
    """Control TV Dashboard mode/room/theme from Admin Panel."""

    url = f"{URL_API_ADMIN}/tv"
    name = "quizrooms:admin_tv"
    requires_auth = True

    async def post(self, request: web.Request) -> web.StreamResponse:
        if denied := _require_ha_admin(request):
            return denied

        try:
            body = await request.json()
        except Exception:
            body = {}

        try:
            tv = await self.coordinator.async_set_tv_mode(
                mode=str(body.get("mode") or "overview"),
                room_code=body.get("room_code"),
                theme=body.get("theme"),
            )
        except EngineError as err:
            return web.json_response(err.to_dict(), status=err.status_code)

        return web.json_response({"ok": True, "tv": tv})


def register_views(hass: Any, coordinator: QuizRoomsCoordinator) -> None:
    """Register all QuizRooms HTTP and WebSocket views on Home Assistant."""
    views = [
        QuizRoomsPlayView(coordinator),
        QuizRoomsTVView(coordinator),
        QuizRoomsAssetsView(coordinator),
        QuizRoomsMediaView(coordinator),
        QuizRoomsPublicBootstrapView(coordinator),
        QuizRoomsWebSocketView(coordinator),
        QuizRoomsAdminPanelJSView(coordinator),
        QuizRoomsAdminStateView(coordinator),
        QuizRoomsAdminPackTemplateView(),
        QuizRoomsAdminPackImportView(coordinator),
        QuizRoomsAdminPackDeleteView(coordinator),
        QuizRoomsAdminRoomActionView(coordinator),
        QuizRoomsAdminTVView(coordinator),
    ]
    for view in views:
        hass.http.register_view(view)
