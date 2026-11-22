"""Home Assistant integration entrypoint for QuizRooms."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import frontend, panel_custom
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from .const import (
    ALLOWED_THEMES,
    ALLOWED_TV_MODES,
    DOMAIN,
    PLATFORMS,
    SERVICE_CLOSE_ROOM,
    SERVICE_OPEN_TV_DASHBOARD,
    SERVICE_PAUSE_ROOM,
    SERVICE_RELOAD_PACKS,
    SERVICE_TV_SET_MODE,
    URL_ADMIN_PANEL_JS,
    URL_SIDEBAR_ICON_JS,
    URL_TV,
    VERSION,
)
from .coordinator import QuizRoomsCoordinator
from .engine import EngineError
from .network_guard import create_network_guard_middleware
from .tv_launcher import TVLaunchError, async_open_tv_dashboard_on_player
from .views import register_views

_LOGGER = logging.getLogger(__name__)

SCHEMA_TV_SET_MODE = vol.Schema(
    {
        vol.Required("mode"): vol.In(ALLOWED_TV_MODES),
        vol.Optional("room_code"): str,
        vol.Optional("theme"): vol.In(ALLOWED_THEMES),
    }
)
SCHEMA_CLOSE_ROOM = vol.Schema({vol.Required("room_code"): str})
SCHEMA_PAUSE_ROOM = vol.Schema(
    {
        vol.Required("room_code"): str,
        vol.Optional("paused"): bool,
    }
)
SCHEMA_RELOAD_PACKS = vol.Schema({})
SCHEMA_OPEN_TV_DASHBOARD = vol.Schema(
    {
        vol.Required("entity_id"): str,
        vol.Optional("url"): str,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up QuizRooms from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    coordinator = QuizRoomsCoordinator.from_hass(hass, options=dict(entry.options))
    await coordinator.async_initialize()
    domain_data["coordinator"] = coordinator
    domain_data["entry_id"] = entry.entry_id

    # Attach aiohttp middleware only if app is not yet frozen (HA freezes hass.http.app
    # once HTTP starts; all unauthenticated QuizRooms views also enforce NetworkGuard directly).
    if not domain_data.get("middleware_registered"):
        app = getattr(getattr(hass, "http", None), "app", None)
        middlewares = getattr(app, "middlewares", None)
        is_frozen = bool(getattr(app, "frozen", False) or getattr(middlewares, "frozen", False))
        if middlewares is not None and not is_frozen:
            middleware = create_network_guard_middleware(
                lambda: hass.data.get(DOMAIN, {}).get("coordinator")
            )
            try:
                middlewares.insert(0, middleware)
                domain_data["middleware_registered"] = True
            except RuntimeError:
                _LOGGER.debug(
                    "hass.http.app.middlewares is frozen; relying on view-level NetworkGuard enforcement."
                )

    # Register HTTP views & WebSocket endpoint once (views dynamically resolve live coordinator)
    if not domain_data.get("views_registered"):
        register_views(hass, coordinator)
        domain_data["views_registered"] = True

    # Register custom HA sidebar icon module (quizrooms:logo) once
    if not domain_data.get("sidebar_icon_registered"):
        add_extra_js = getattr(frontend, "add_extra_js_url", None)
        if callable(add_extra_js):
            add_extra_js(hass, f"{URL_SIDEBAR_ICON_JS}?v={VERSION}")
        domain_data["sidebar_icon_registered"] = True

    # Register HA Admin Sidebar Panel (clean up any stale registration on reload)
    if DOMAIN in (hass.data.get("frontend_panels") or {}):
        try:
            frontend.async_remove_panel(hass, DOMAIN)
        except Exception:  # pragma: no cover - defensive across HA versions
            pass

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="quizrooms-admin-panel",
        frontend_url_path=DOMAIN,
        sidebar_title="QuizRooms",
        sidebar_icon="quizrooms:logo",
        module_url=f"{URL_ADMIN_PANEL_JS}?v={VERSION}",
        require_admin=True,
        config={"domain": DOMAIN},
    )

    # Register HA Services
    async def handle_tv_set_mode(call: ServiceCall) -> None:
        try:
            await coordinator.async_set_tv_mode(
                mode=call.data["mode"],
                room_code=call.data.get("room_code"),
                theme=call.data.get("theme"),
                is_admin=True,
            )
        except EngineError as err:
            raise HomeAssistantError(err.message) from err

    async def handle_close_room(call: ServiceCall) -> None:
        try:
            await coordinator.async_close_room(
                call.data["room_code"],
                is_admin=True,
                reason="Room closed via Home Assistant service.",
            )
        except EngineError as err:
            raise HomeAssistantError(err.message) from err

    async def handle_pause_room(call: ServiceCall) -> None:
        try:
            await coordinator.async_pause_room(
                call.data["room_code"],
                paused=call.data.get("paused"),
                is_admin=True,
            )
        except EngineError as err:
            raise HomeAssistantError(err.message) from err

    async def handle_reload_packs(call: ServiceCall) -> None:
        await coordinator.async_reload_packs()

    async def handle_open_tv_dashboard(call: ServiceCall) -> None:
        target_url = call.data.get("url") or f"{coordinator.resolve_lan_base_url()}{URL_TV}"
        try:
            await async_open_tv_dashboard_on_player(
                hass,
                entity_id=call.data["entity_id"],
                tv_url=target_url,
            )
        except TVLaunchError as err:
            raise HomeAssistantError(err.message) from err

    hass.services.async_register(
        DOMAIN, SERVICE_TV_SET_MODE, handle_tv_set_mode, schema=SCHEMA_TV_SET_MODE
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_OPEN_TV_DASHBOARD,
        handle_open_tv_dashboard,
        schema=SCHEMA_OPEN_TV_DASHBOARD,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLOSE_ROOM, handle_close_room, schema=SCHEMA_CLOSE_ROOM
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PAUSE_ROOM, handle_pause_room, schema=SCHEMA_PAUSE_ROOM
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RELOAD_PACKS, handle_reload_packs, schema=SCHEMA_RELOAD_PACKS
    )

    # Dynamic OptionsFlow update listener (no restart required)
    async def _async_options_updated(hass_inst: HomeAssistant, updated_entry: ConfigEntry) -> None:
        coordinator.apply_options(dict(updated_entry.options))
        _LOGGER.info("QuizRooms network & runtime options updated dynamically.")

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, list(PLATFORMS))
    coordinator.start_background_tasks()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a QuizRooms config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, list(PLATFORMS))
    if not unload_ok:
        return False

    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})
    coordinator: QuizRoomsCoordinator | None = domain_data.pop("coordinator", None)
    if coordinator is not None:
        await coordinator.async_shutdown()

    for service in (
        SERVICE_TV_SET_MODE,
        SERVICE_OPEN_TV_DASHBOARD,
        SERVICE_CLOSE_ROOM,
        SERVICE_PAUSE_ROOM,
        SERVICE_RELOAD_PACKS,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)

    try:
        frontend.async_remove_panel(hass, DOMAIN)
    except Exception:  # pragma: no cover - defensive across HA versions
        pass

    return True
