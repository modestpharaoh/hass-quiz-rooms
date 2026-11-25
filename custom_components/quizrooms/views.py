"""HTTP views and WebSocket handler for QuizRooms (Guest, TV, Media, and Admin APIs)."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web
from homeassistant.components.http import HomeAssistantView

from .avatars import AvatarError, MAX_AVATAR_UPLOAD_BYTES, list_preset_catalog
from .const import (
    DOMAIN,
    MAX_JSON_BODY_BYTES,
    MAX_MULTIPART_PARTS,
    MAX_UPLOAD_BYTES,
    MAX_WS_MESSAGE_BYTES,
    PHASE_QUESTION,
    URL_ADMIN_PANEL_JS,
    URL_API_ADMIN,
    URL_API_PUBLIC,
    URL_ASSETS,
    URL_AVATAR,
    URL_MEDIA,
    URL_PLAY,
    URL_SIDEBAR_ICON_JS,
    URL_TV,
    URL_WS,
    VERSION,
)
from .coordinator import QuizRoomsCoordinator, WSClient
from .engine import EngineError
from .network_guard import build_forbidden_response
from .packs import PackValidationError, sample_pack_template
from .stage_effects import (
    StageEffectError,
    discover_stage_lights,
    discover_stage_speakers_and_tts,
)
from .tv_launcher import (
    TVLaunchError,
    async_open_tv_dashboard_on_player,
    discover_supported_tv_players,
)

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

# Hardened headers for HTML pages (prevents Clickjacking & Referer room-code leakage)
HTML_SECURITY_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
}


def _is_ws_origin_allowed(coordinator: QuizRoomsCoordinator, request: web.Request) -> bool:
    """Protect /quizrooms/ws against Cross-Site WebSocket Hijacking (CSWSH) from external sites."""
    headers = getattr(request, "headers", None) or {}
    origin = (headers.get("Origin") or headers.get("origin") or "").strip()
    if not origin:
        return True
    # Reject sandboxed-iframe / data: opaque origins ("null") which external sites can forge
    if origin.lower() == "null":
        return False
    try:
        parsed_origin = urlsplit(origin)
    except ValueError:
        return False
    if parsed_origin.scheme not in ("http", "https", "ws", "wss"):
        return False
    origin_host = (parsed_origin.netloc or "").strip().lower()
    if not origin_host:
        return False

    allowed_hosts: set[str] = set()
    req_host = str(getattr(request, "host", "") or headers.get("Host") or "").strip().lower()
    if req_host:
        allowed_hosts.add(req_host)
        allowed_hosts.add(req_host.split(":", 1)[0])

    extra_urls = [coordinator.custom_lan_base_url]
    ha_config = getattr(coordinator.hass, "config", None)
    if ha_config is not None:
        extra_urls.append(getattr(ha_config, "internal_url", None) or "")
        extra_urls.append(getattr(ha_config, "external_url", None) or "")

    for candidate_url in extra_urls:
        if not candidate_url:
            continue
        try:
            extra_host = (urlsplit(str(candidate_url)).netloc or "").strip().lower()
            if extra_host:
                allowed_hosts.add(extra_host)
                allowed_hosts.add(extra_host.split(":", 1)[0])
        except ValueError:
            pass

    if not allowed_hosts:
        return True

    return origin_host in allowed_hosts or origin_host.split(":", 1)[0] in allowed_hosts


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


async def _read_bounded_body(request: web.Request, max_bytes: int) -> bytes | None:
    """Stream-read an HTTP request body up to max_bytes; return None if limit exceeded."""
    content_length = getattr(request, "content_length", None)
    if isinstance(content_length, int) and content_length > max_bytes:
        return None

    content = getattr(request, "content", None)
    if content is not None and hasattr(content, "iter_chunked"):
        chunks: list[bytes] = []
        total = 0
        async for chunk in content.iter_chunked(32768):
            total += len(chunk)
            if total > max_bytes:
                return None
            chunks.append(chunk)
        return b"".join(chunks)

    data = await request.read()
    if len(data) > max_bytes:
        return None
    return data


async def _read_bounded_json(
    request: web.Request, max_bytes: int = MAX_JSON_BODY_BYTES
) -> tuple[dict[str, Any], web.Response | None]:
    """Read a JSON object up to max_bytes; returns (body_dict, error_response_if_too_large)."""
    content_length = getattr(request, "content_length", None)
    if isinstance(content_length, int) and content_length > max_bytes:
        return {}, web.json_response(
            {"error": "payload_too_large", "message": "Request JSON body is too large."},
            status=413,
        )

    has_stream = getattr(request, "content", None) is not None or (
        "read" in type(request).__dict__
    )
    if has_stream:
        raw_bytes = await _read_bounded_body(request, max_bytes)
        if raw_bytes is None:
            return {}, web.json_response(
                {"error": "payload_too_large", "message": "Request JSON body is too large."},
                status=413,
            )
        if not raw_bytes:
            return {}, None
        try:
            parsed = json.loads(raw_bytes.decode("utf-8"))
        except Exception:
            return {}, None
        return (parsed if isinstance(parsed, dict) else {}), None

    try:
        parsed = await request.json()
    except Exception:
        return {}, None
    return (parsed if isinstance(parsed, dict) else {}), None


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
            headers=dict(HTML_SECURITY_HEADERS),
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
            headers=dict(HTML_SECURITY_HEADERS),
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
        query = getattr(request, "query", None) or {}
        cache_control = (
            "public, max-age=31536000, immutable"
            if query.get("v")
            else "public, max-age=3600"
        )
        return web.Response(
            body=data,
            headers={
                "Content-Type": content_type,
                "Cache-Control": cache_control,
                "X-Content-Type-Options": "nosniff",
            },
        )


class QuizRoomsMediaView(_QuizRoomsBaseView):
    """Serve pack images and covers via opaque IDs at /quizrooms/media/{opaque_id} with strict sandbox CSP.

    Permits LAN guests/TV AND authenticated Home Assistant users so the Admin Sidebar Panel
    renders pack cover photos cleanly both locally and remotely.
    """

    url = f"{URL_MEDIA}/{{opaque_id}}"
    name = "quizrooms:media"
    requires_auth = False

    async def get(self, request: web.Request, opaque_id: str) -> web.StreamResponse:
        if request.get("hass_user") is None:
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
    """Return initial lobby state, packs, TV state (with followed room), and LAN base URL."""

    url = f"{URL_API_PUBLIC}/bootstrap"
    name = "quizrooms:public_bootstrap"
    requires_auth = False

    async def get(self, request: web.Request) -> web.StreamResponse:
        if blocked := _check_network_guard(self.coordinator, request):
            return blocked

        followed_room = None
        if self.coordinator.tv_state.mode == "follow" and self.coordinator.tv_state.room_code:
            followed_room = self.coordinator.engine.get_room(self.coordinator.tv_state.room_code)
            if followed_room is None:
                # Auto-reset stale follow target back to overview
                self.coordinator.tv_state.mode = "overview"
                self.coordinator.tv_state.room_code = None

        return web.json_response(
            {
                "lobby": self.coordinator.engine.serialize_public_lobby(),
                "packs": [p.to_summary() for p in self.coordinator.pack_registry.list_packs()],
                "avatars": list_preset_catalog(self.coordinator.engine.get_taken_preset_avatars()),
                "tv": self.coordinator.tv_state.to_dict(),
                "room": (
                    self.coordinator.engine.serialize_room_state(followed_room, for_session_token=None)
                    if followed_room is not None
                    else None
                ),
                "lan_base_url": self.coordinator.resolve_lan_base_url(request),
            }
        )


class QuizRoomsPublicAvatarUploadView(_QuizRoomsBaseView):
    """Upload a custom profile photo (LAN-only, max 256 KB, PNG/JPEG/WebP/GIF only)."""

    url = f"{URL_API_PUBLIC}/avatar"
    name = "quizrooms:public_avatar_upload"
    requires_auth = False

    async def post(self, request: web.Request) -> web.StreamResponse:
        if blocked := _check_network_guard(self.coordinator, request):
            return blocked

        file_bytes = b""
        content_type = request.content_type or ""
        if content_type.startswith("multipart/"):
            content_length = getattr(request, "content_length", None)
            if isinstance(content_length, int) and content_length > (MAX_AVATAR_UPLOAD_BYTES + 16384):
                return web.json_response(
                    {
                        "error": "avatar_too_large",
                        "message": f"Profile photo must be under {MAX_AVATAR_UPLOAD_BYTES // 1024} KB.",
                    },
                    status=413,
                )
            reader = await request.multipart()
            part_count = 0
            total_stream_bytes = 0
            while (part := await reader.next()) is not None:
                part_count += 1
                if part_count > MAX_MULTIPART_PARTS:
                    return web.json_response(
                        {"error": "too_many_parts", "message": "Too many multipart fields."},
                        status=400,
                    )
                chunks: list[bytes] = []
                part_bytes = 0
                while True:
                    chunk = await part.read_chunk(32768)
                    if not chunk:
                        break
                    part_bytes += len(chunk)
                    total_stream_bytes += len(chunk)
                    if part_bytes > MAX_AVATAR_UPLOAD_BYTES or total_stream_bytes > (MAX_AVATAR_UPLOAD_BYTES + 16384):
                        return web.json_response(
                            {
                                "error": "avatar_too_large",
                                "message": f"Profile photo must be under {MAX_AVATAR_UPLOAD_BYTES // 1024} KB.",
                            },
                            status=413,
                        )
                    if part.name == "file" and not file_bytes:
                        chunks.append(chunk)
                if part.name == "file" and not file_bytes:
                    file_bytes = b"".join(chunks)
        else:
            bounded = await _read_bounded_body(request, MAX_AVATAR_UPLOAD_BYTES)
            if bounded is None:
                return web.json_response(
                    {
                        "error": "avatar_too_large",
                        "message": f"Profile photo must be under {MAX_AVATAR_UPLOAD_BYTES // 1024} KB.",
                    },
                    status=413,
                )
            file_bytes = bounded

        try:
            avatar_ref, avatar_url = self.coordinator.avatar_store.store_custom_upload(file_bytes)
        except AvatarError as err:
            return web.json_response(
                {"error": err.code, "message": err.message},
                status=err.status_code,
            )

        return web.json_response({"ok": True, "avatar": avatar_ref, "avatar_url": avatar_url}, status=201)


class QuizRoomsAvatarView(_QuizRoomsBaseView):
    """Serve preset SVG avatars and custom profile photos with strict sandbox CSP.

    Permits LAN guests/TV AND authenticated Home Assistant users so the Admin Sidebar Panel
    renders player avatars cleanly both locally and remotely.
    """

    url = f"{URL_AVATAR}/{{kind}}/{{avatar_id}}"
    name = "quizrooms:avatar"
    requires_auth = False

    async def get(self, request: web.Request, kind: str, avatar_id: str) -> web.StreamResponse:
        hass_user = request.get("hass_user")
        if hass_user is None:
            if blocked := _check_network_guard(self.coordinator, request):
                return blocked

        found: tuple[bytes, str] | None = None
        if kind == "preset":
            found = await self.coordinator.async_run_in_executor(
                self.coordinator.avatar_store.get_preset_svg_bytes, avatar_id
            )
        elif kind == "custom":
            found = self.coordinator.avatar_store.get_custom_bytes(avatar_id)

        if found is None:
            return web.Response(status=404, text="Avatar not found", headers=dict(MEDIA_SECURITY_HEADERS))

        data, content_type = found
        headers = dict(MEDIA_SECURITY_HEADERS)
        headers["Content-Type"] = content_type
        return web.Response(body=data, headers=headers)


class QuizRoomsPublicTVModeView(_QuizRoomsBaseView):
    """Allow local-network TV displays to switch Overview/Follow mode via HTTP fallback."""

    url = f"{URL_API_PUBLIC}/tv_mode"
    name = "quizrooms:public_tv_mode"
    requires_auth = False

    async def post(self, request: web.Request) -> web.StreamResponse:
        if blocked := _check_network_guard(self.coordinator, request):
            return blocked

        body, too_large = await _read_bounded_json(request)
        if too_large is not None:
            return too_large

        try:
            tv = await self.coordinator.async_set_tv_mode(
                mode=str(body.get("mode") or "overview"),
                room_code=body.get("room_code"),
                theme=body.get("theme"),
                is_admin=False,
            )
        except EngineError as err:
            return web.json_response(err.to_dict(), status=err.status_code)

        followed_room = (
            self.coordinator.engine.get_room(tv["room_code"])
            if tv.get("mode") == "follow" and tv.get("room_code")
            else None
        )
        return web.json_response(
            {
                "ok": True,
                "tv": tv,
                "room": (
                    self.coordinator.engine.serialize_room_state(followed_room, for_session_token=None)
                    if followed_room is not None
                    else None
                ),
                "lobby": self.coordinator.engine.serialize_public_lobby(),
            }
        )


class QuizRoomsWebSocketView(_QuizRoomsBaseView):
    """Multiplexed WebSocket endpoint at /quizrooms/ws for Lobby, Players, and TV displays."""

    url = URL_WS
    name = "quizrooms:ws"
    requires_auth = False

    async def get(self, request: web.Request) -> web.StreamResponse:
        decision = self.coordinator.network_guard.evaluate_request(request)
        if not decision.allowed:
            return build_forbidden_response(request, decision)
        if not _is_ws_origin_allowed(self.coordinator, request):
            return web.json_response(
                {
                    "error": "cross_origin_ws_blocked",
                    "message": "Cross-origin WebSocket connections are not permitted.",
                },
                status=403,
            )
        if not self.coordinator.can_accept_ws(decision.client_ip):
            return web.json_response(
                {
                    "error": "ws_limit_reached",
                    "message": "Too many active WebSocket connections.",
                },
                status=429,
            )

        ws = web.WebSocketResponse(heartbeat=25.0, max_msg_size=MAX_WS_MESSAGE_BYTES)
        await ws.prepare(request)

        role = request.query.get("role", "lobby").strip().lower()
        if role not in ("lobby", "room", "tv"):
            role = "lobby"

        client = WSClient(ws=ws, role=role, client_ip=decision.client_ip)
        self.coordinator.register_ws(client)

        try:
            followed_room = None
            if role == "tv" and self.coordinator.tv_state.mode == "follow" and self.coordinator.tv_state.room_code:
                followed_room = self.coordinator.engine.get_room(self.coordinator.tv_state.room_code)
                if followed_room is None:
                    self.coordinator.tv_state.mode = "overview"
                    self.coordinator.tv_state.room_code = None

            # Push initial snapshot on connect (includes followed room atomically for TV)
            await ws.send_json(
                {
                    "type": "hello",
                    "role": role,
                    "lan_base_url": self.coordinator.resolve_lan_base_url(request),
                    "lobby": self.coordinator.engine.serialize_public_lobby(),
                    "packs": [p.to_summary() for p in self.coordinator.pack_registry.list_packs()],
                    "avatars": list_preset_catalog(self.coordinator.engine.get_taken_preset_avatars()),
                    "tv": self.coordinator.tv_state.to_dict(),
                    "room": (
                        self.coordinator.engine.serialize_room_state(followed_room, for_session_token=None)
                        if followed_room is not None
                        else None
                    ),
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
                client.session_token = None
                await client.ws.send_json(
                    {
                        "type": "lobby_update",
                        "lobby": engine.serialize_public_lobby(),
                        "avatars": list_preset_catalog(engine.get_taken_preset_avatars()),
                    }
                )
                return

            if msg_type == "create_room":
                room, session_token, host_token = engine.create_room(
                    host_session_token=data.get("session_token"),
                    host_nickname=str(data.get("nickname") or ""),
                    host_avatar=data.get("avatar"),
                    room_name=data.get("room_name"),
                    pack_ids=data.get("pack_ids"),
                    round_count=data.get("round_count"),
                    timer_seconds=data.get("timer_seconds"),
                    answer_mode=data.get("answer_mode"),
                    wager_mode=data.get("wager_mode"),
                    deduct_wrong_wager=data.get("deduct_wrong_wager"),
                    unique_answers_only=data.get("unique_answers_only"),
                    difficulty=data.get("difficulty"),
                    visibility=data.get("visibility"),
                    allow_late_join=data.get("allow_late_join"),
                    bonus_round=data.get("bonus_round"),
                    kids_mode=data.get("kids_mode"),
                    kids_try_again=data.get("kids_try_again"),
                    kids_auto_advance=data.get("kids_auto_advance"),
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
                    avatar=data.get("avatar"),
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

            if msg_type == "update_avatar":
                bound_code = str(client.room_code or "")
                req_code = str(data.get("code") or bound_code).strip().upper()
                session_token = str(client.session_token or "")
                if client.role != "room" or not bound_code or not session_token:
                    raise EngineError("not_in_room", "Join the room before updating your avatar.")
                if req_code != bound_code:
                    raise EngineError("room_mismatch", "Socket is bound to a different room.", status_code=403)
                room, _player = engine.update_player_avatar(
                    bound_code,
                    session_token=session_token,
                    avatar=str(data.get("avatar") or ""),
                )
                await self.coordinator.broadcast_room_state(room.code)
                await self.coordinator.broadcast_lobby_and_tv()
                return

            if msg_type == "leave_room":
                old_code = client.room_code
                old_token = client.session_token
                client.role = "lobby"
                client.room_code = None
                client.session_token = None
                if old_code and old_token:
                    engine.leave_room(old_code, old_token)
                    await self.coordinator.broadcast_room_state(old_code)
                    await self.coordinator.broadcast_lobby_and_tv()
                    self.coordinator.notify_entities()
                await client.ws.send_json(
                    {
                        "type": "lobby_update",
                        "lobby": engine.serialize_public_lobby(),
                        "avatars": list_preset_catalog(engine.get_taken_preset_avatars()),
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
                    deduct_wrong_wager=data.get("deduct_wrong_wager"),
                    unique_answers_only=data.get("unique_answers_only"),
                    difficulty=data.get("difficulty"),
                    visibility=data.get("visibility"),
                    allow_late_join=data.get("allow_late_join"),
                    bonus_round=data.get("bonus_round"),
                    kids_mode=data.get("kids_mode"),
                    kids_try_again=data.get("kids_try_again"),
                    kids_auto_advance=data.get("kids_auto_advance"),
                )
                await self.coordinator.broadcast_room_state(room.code)
                await self.coordinator.broadcast_lobby_and_tv()
                self.coordinator.notify_entities()
                return

            if msg_type == "start_game":
                code = str(data.get("code") or client.room_code or "")
                await self.coordinator.async_start_game(code, host_token=data.get("host_token"))
                return

            if msg_type == "submit_bonus_wager":
                bound_code = str(client.room_code or "")
                req_code = str(data.get("code") or bound_code).strip().upper()
                session_token = str(client.session_token or "")
                if client.role != "room" or not bound_code or not session_token:
                    raise EngineError("not_in_room", "Join the room before placing a bonus wager.")
                if req_code != bound_code:
                    raise EngineError("room_mismatch", "Socket is bound to a different room.", status_code=403)
                room, _chosen = engine.submit_bonus_wager(
                    bound_code,
                    session_token=session_token,
                    wager=data.get("wager"),
                )
                await self.coordinator.broadcast_room_state(room.code)
                return

            if msg_type == "submit_answer":
                bound_code = str(client.room_code or "")
                req_code = str(data.get("code") or bound_code).strip().upper()
                session_token = str(client.session_token or "")
                if client.role != "room" or not bound_code or not session_token:
                    raise EngineError("not_in_room", "Join the room before submitting an answer.")
                if req_code != bound_code:
                    raise EngineError("room_mismatch", "Socket is bound to a different room.", status_code=403)
                room, sub, all_locked = engine.submit_answer(
                    bound_code,
                    session_token=session_token,
                    answer=str(data.get("answer") or ""),
                    wager=data.get("wager"),
                )
                if sub.outcome == "try_again":
                    cur_q = (
                        room.questions[room.current_index]
                        if 0 <= room.current_index < len(room.questions)
                        else None
                    )
                    is_rtl = bool(cur_q.rtl if cur_q is not None else room.pack_rtl)
                    await client.ws.send_json(
                        {
                            "type": "try_again",
                            "code": room.code,
                            "answer": sub.answer,
                            "rtl": is_rtl,
                            "message": (
                                "محاولة جميلة! جرّب خياراً آخر!"
                                if is_rtl
                                else "Good try! Tap another one!"
                            ),
                        }
                    )
                    return
                await self.coordinator.broadcast_room_state(room.code)
                if all_locked:
                    # Crisp beat before auto-reveal (tracked in coordinator._room_timers)
                    self.coordinator.schedule_auto_reveal(room.code, room.current_index, delay=0.7)
                return

            if msg_type == "next_step":
                code = str(data.get("code") or client.room_code or "")
                await self.coordinator.async_next_step(code, host_token=data.get("host_token"))
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
                await self.coordinator.async_play_again(code, host_token=data.get("host_token"))
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


# ---------------------------------------------------------------------------
# Authenticated Home Assistant Admin Panel & Admin APIs
# ---------------------------------------------------------------------------


def _read_admin_panel_bundle_sync(frontend_dir: Path) -> str:
    qr_js = (frontend_dir / "assets" / "js" / "qrcode.js").read_text(encoding="utf-8")
    panel_js = (frontend_dir / "admin-panel.js").read_text(encoding="utf-8")
    return f"{qr_js}\n;\n{panel_js}"


class QuizRoomsAdminPanelJSView(_QuizRoomsBaseView):
    """Serve the HA Sidebar Admin Panel JS module."""

    url = URL_ADMIN_PANEL_JS
    name = "quizrooms:admin_panel_js"
    requires_auth = False  # Browser module import from HA sidebar; all data APIs require HA admin auth

    async def get(self, request: web.Request) -> web.StreamResponse:
        js_text = await self.coordinator.async_run_in_executor(
            _read_admin_panel_bundle_sync, self.coordinator.frontend_dir
        )
        return web.Response(
            text=js_text,
            content_type="application/javascript",
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )


class QuizRoomsSidebarIconJSView(_QuizRoomsBaseView):
    """Serve the HA custom sidebar icon registration module (quizrooms:logo)."""

    url = URL_SIDEBAR_ICON_JS
    name = "quizrooms:sidebar_icon_js"
    requires_auth = False  # Loaded globally by HA frontend so sidebar icon renders for all admins

    async def get(self, request: web.Request) -> web.StreamResponse:
        js_path = self.coordinator.frontend_dir / "sidebar-icon.js"
        js_text = await self.coordinator.async_run_in_executor(_read_text_sync, js_path)
        return web.Response(
            text=js_text,
            content_type="application/javascript",
            headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )


class QuizRoomsAdminStateView(_QuizRoomsBaseView):
    """Return full admin state (all rooms, packs, max_rooms, TV state)."""

    url = f"{URL_API_ADMIN}/state"
    name = "quizrooms:admin_state"
    requires_auth = True

    async def get(self, request: web.Request) -> web.StreamResponse:
        if denied := _require_ha_admin(request):
            return denied

        audio_targets = discover_stage_speakers_and_tts(self.coordinator.hass)
        stage_available = self.coordinator.is_stage_events_allowed()
        return web.json_response(
            {
                "version": VERSION,
                "rooms": self.coordinator.engine.serialize_admin_rooms(),
                "active_rooms": self.coordinator.engine.active_room_count,
                "active_players": self.coordinator.engine.active_player_count,
                "max_rooms": self.coordinator.engine.max_rooms,
                "packs": [p.to_summary() for p in self.coordinator.pack_registry.list_packs()],
                "tv": self.coordinator.tv_state.to_dict(),
                "tv_targets": discover_supported_tv_players(self.coordinator.hass),
                "lan_base_url": self.coordinator.resolve_lan_base_url(request),
                "allow_external_access": self.coordinator.network_guard.allow_external_access,
                "stage_effects": {
                    "available": stage_available,
                    "disabled_reason": None if stage_available else "external_access_enabled",
                    "config": self.coordinator.stage_director.config.to_dict(),
                    "lights": discover_stage_lights(self.coordinator.hass),
                    "speakers": audio_targets["speakers"],
                    "tts_engines": audio_targets["tts_engines"],
                },
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
            content_length = getattr(request, "content_length", None)
            if isinstance(content_length, int) and content_length > (MAX_UPLOAD_BYTES + 65536):
                return web.json_response(
                    {
                        "error": "file_too_large",
                        "message": f"Upload exceeds maximum size of {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                        "details": [f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."],
                    },
                    status=413,
                )
            reader = await request.multipart()
            part_count = 0
            total_stream_bytes = 0
            while (part := await reader.next()) is not None:
                part_count += 1
                if part_count > MAX_MULTIPART_PARTS:
                    return web.json_response(
                        {
                            "error": "too_many_parts",
                            "message": "Too many multipart form fields.",
                            "details": ["Too many multipart form fields."],
                        },
                        status=400,
                    )
                if part.name == "overwrite":
                    ov_chunk = await part.read_chunk(64)
                    total_stream_bytes += len(ov_chunk)
                    val = ov_chunk.decode("utf-8", errors="ignore").strip().lower()
                    overwrite = val in ("1", "true", "yes")
                elif part.name == "file" and not file_bytes:
                    filename = part.filename or ""
                    chunks: list[bytes] = []
                    total = 0
                    while True:
                        chunk = await part.read_chunk(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        total_stream_bytes += len(chunk)
                        if total > MAX_UPLOAD_BYTES or total_stream_bytes > (MAX_UPLOAD_BYTES + 65536):
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
                    while True:
                        chunk = await part.read_chunk(65536)
                        if not chunk:
                            break
                        total_stream_bytes += len(chunk)
                        if total_stream_bytes > (MAX_UPLOAD_BYTES + 65536):
                            return web.json_response(
                                {
                                    "error": "file_too_large",
                                    "message": f"Upload exceeds maximum size of {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                                    "details": [f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."],
                                },
                                status=413,
                            )
        else:
            filename = request.headers.get("X-Pack-Filename", "pack.json")
            bounded = await _read_bounded_body(request, MAX_UPLOAD_BYTES)
            if bounded is None:
                return web.json_response(
                    {
                        "error": "file_too_large",
                        "message": f"Upload exceeds maximum size of {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                        "details": [f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."],
                    },
                    status=413,
                )
            file_bytes = bounded

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

        await self.coordinator.broadcast_lobby_and_tv(include_packs=True)
        return web.json_response({"ok": True, "pack": pack.to_summary()}, status=201)


class QuizRoomsAdminPackPreviewView(_QuizRoomsBaseView):
    """Return difficulty breakdown and sample questions for a trivia pack (HA Admin only)."""

    url = f"{URL_API_ADMIN}/packs/{{pack_id}}/preview"
    name = "quizrooms:admin_pack_preview"
    requires_auth = True

    async def get(self, request: web.Request, pack_id: str) -> web.StreamResponse:
        if denied := _require_ha_admin(request):
            return denied

        pack = self.coordinator.pack_registry.get_pack(pack_id)
        if pack is None:
            return web.json_response(
                {"error": "pack_not_found", "message": f"Pack '{pack_id}' was not found."},
                status=404,
            )

        # Pick balanced sample questions across easy / medium / hard tiers (up to 6)
        samples = []
        seen_ids: set[str] = set()
        for tier in ("easy", "medium", "hard"):
            tier_qs = [q for q in pack.questions if q.difficulty == tier]
            for q in tier_qs[:2]:
                if q.id not in seen_ids:
                    samples.append(q)
                    seen_ids.add(q.id)
        if len(samples) < 6:
            for q in pack.questions:
                if q.id not in seen_ids:
                    samples.append(q)
                    seen_ids.add(q.id)
                if len(samples) >= 6:
                    break

        return web.json_response(
            {
                "ok": True,
                "pack": pack.to_summary(),
                "total_questions": len(pack.questions),
                "sample_questions": [
                    {
                        "id": q.id,
                        "prompt": q.prompt,
                        "answer": q.answer,
                        "choices": list(q.choices),
                        "difficulty": q.difficulty,
                        "open_ended": q.open_ended,
                        "starts_with": q.starts_with,
                        "rtl": q.rtl,
                        "media_url": f"{URL_MEDIA}/{q.media_id}" if q.media_id else None,
                        "choice_style": q.choice_style,
                        "choice_media_urls": [
                            f"{URL_MEDIA}/{mid}" if mid else None for mid in q.choice_media_ids
                        ],
                        "choice_colors": list(q.choice_colors),
                        "spoken_prompt": q.spoken_prompt,
                    }
                    for q in samples
                ],
            }
        )


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

        self.coordinator.prune_unused_pinned_media()
        await self.coordinator.broadcast_lobby_and_tv(include_packs=True)
        return web.json_response({"ok": True, "deleted": pack_id})


class QuizRoomsAdminRoomActionView(_QuizRoomsBaseView):
    """Allow HA Admins to advance, pause, resume, or force-close any room."""

    url = f"{URL_API_ADMIN}/rooms/{{code}}/action"
    name = "quizrooms:admin_room_action"
    requires_auth = True

    async def post(self, request: web.Request, code: str) -> web.StreamResponse:
        if denied := _require_ha_admin(request):
            return denied

        body, too_large = await _read_bounded_json(request)
        if too_large is not None:
            return too_large

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
                await self.coordinator.async_next_step(code, is_admin=True)
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

        body, too_large = await _read_bounded_json(request)
        if too_large is not None:
            return too_large

        try:
            tv = await self.coordinator.async_set_tv_mode(
                mode=str(body.get("mode") or "overview"),
                room_code=body.get("room_code"),
                theme=body.get("theme"),
                is_admin=True,
            )
        except EngineError as err:
            return web.json_response(err.to_dict(), status=err.status_code)

        return web.json_response({"ok": True, "tv": tv})


class QuizRoomsAdminTVOpenView(_QuizRoomsBaseView):
    """Open the QuizRooms TV Dashboard URL on a connected supported Smart TV media player."""

    url = f"{URL_API_ADMIN}/tv/open"
    name = "quizrooms:admin_tv_open"
    requires_auth = True

    async def post(self, request: web.Request) -> web.StreamResponse:
        if denied := _require_ha_admin(request):
            return denied

        body, too_large = await _read_bounded_json(request)
        if too_large is not None:
            return too_large

        entity_id = str(body.get("entity_id") or "").strip()
        tv_url = str(body.get("url") or "").strip() or f"{self.coordinator.resolve_lan_base_url(request)}{URL_TV}"

        try:
            result = await async_open_tv_dashboard_on_player(
                self.coordinator.hass,
                entity_id=entity_id,
                tv_url=tv_url,
            )
        except TVLaunchError as err:
            return web.json_response(
                {"error": err.code, "message": err.message},
                status=err.status_code,
            )

        return web.json_response({"ok": True, **result})


class QuizRoomsAdminStageView(_QuizRoomsBaseView):
    """Configure and test TV-synced Smart-Home Stage Effects (strictly LAN-Only mode)."""

    url = f"{URL_API_ADMIN}/stage"
    name = "quizrooms:admin_stage"
    requires_auth = True

    async def post(self, request: web.Request) -> web.StreamResponse:
        if denied := _require_ha_admin(request):
            return denied

        body, too_large = await _read_bounded_json(request)
        if too_large is not None:
            return too_large

        action = str(body.get("action") or "save").strip().lower()
        try:
            if action == "test":
                if not self.coordinator.is_stage_events_allowed():
                    raise StageEffectError(
                        "stage_effects_disabled_external_mode",
                        "Smart-Home Stage Effects & HA Events are disabled while External Access is enabled.",
                        status_code=409,
                    )
                if isinstance(body.get("config"), dict):
                    await self.coordinator.async_update_stage_config(body["config"])
                effect = str(body.get("effect") or "").strip()
                raw_lang = body.get("language")
                language = str(raw_lang).strip() if raw_lang else None
                result = await self.coordinator.async_run_stage_test(effect, language=language)
                return web.json_response(
                    {
                        "ok": True,
                        "config": self.coordinator.stage_director.config.to_dict(),
                        **result,
                    }
                )

            raw_cfg = body.get("config") if isinstance(body.get("config"), dict) else body
            updated = await self.coordinator.async_update_stage_config(raw_cfg)
            return web.json_response(
                {
                    "ok": True,
                    "config": updated,
                    "message": "Smart-Home Stage Effects settings saved.",
                }
            )
        except StageEffectError as err:
            return web.json_response(
                {"error": err.code, "message": err.message},
                status=err.status_code,
            )


def register_views(hass: Any, coordinator: QuizRoomsCoordinator) -> None:
    """Register all QuizRooms HTTP and WebSocket views on Home Assistant."""
    views = [
        QuizRoomsPlayView(coordinator),
        QuizRoomsTVView(coordinator),
        QuizRoomsAssetsView(coordinator),
        QuizRoomsMediaView(coordinator),
        QuizRoomsAvatarView(coordinator),
        QuizRoomsPublicBootstrapView(coordinator),
        QuizRoomsPublicAvatarUploadView(coordinator),
        QuizRoomsPublicTVModeView(coordinator),
        QuizRoomsWebSocketView(coordinator),
        QuizRoomsAdminPanelJSView(coordinator),
        QuizRoomsSidebarIconJSView(coordinator),
        QuizRoomsAdminStateView(coordinator),
        QuizRoomsAdminPackTemplateView(),
        QuizRoomsAdminPackImportView(coordinator),
        QuizRoomsAdminPackPreviewView(coordinator),
        QuizRoomsAdminPackDeleteView(coordinator),
        QuizRoomsAdminRoomActionView(coordinator),
        QuizRoomsAdminTVView(coordinator),
        QuizRoomsAdminTVOpenView(coordinator),
        QuizRoomsAdminStageView(coordinator),
    ]
    for view in views:
        hass.http.register_view(view)
