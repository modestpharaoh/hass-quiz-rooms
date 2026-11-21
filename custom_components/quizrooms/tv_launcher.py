"""Discovery and URL launching for supported Home Assistant Smart TV media players.

Supports only TV integrations capable of opening web pages or TV browsers:
- LG webOS Smart TV (`webostv`) via `webostv.command` (`system.launcher/open`)
- Android TV ADB (`androidtv`) via `androidtv.adb_command` (`am start -a android.intent.action.VIEW`)
- Android TV / Google TV Remote (`androidtv_remote`) via `media_player.play_media` (url) / `remote.turn_on` (activity)
- Samsung Smart TV (`samsungtv`, `samsungtv_smart`, `samsungtv_tizen`) via `media_player.play_media`
  (`browser` / `url`, with automatic fallback to launching `org.tizen.browser`).
"""

from __future__ import annotations

import logging
import shlex
from typing import Any

from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

# Strictly allowlisted HA platforms for Smart TV media_player entities
SUPPORTED_TV_PLATFORMS: dict[str, dict[str, str]] = {
    "webostv": {
        "label": "LG webOS TV",
        "method": "webostv.command (system.launcher/open)",
    },
    "androidtv": {
        "label": "Android TV (ADB)",
        "method": "androidtv.adb_command (browser VIEW intent)",
    },
    "androidtv_remote": {
        "label": "Android TV / Google TV",
        "method": "media_player.play_media (url) / remote.turn_on",
    },
    "samsungtv": {
        "label": "Samsung Smart TV",
        "method": "media_player.play_media (url / org.tizen.browser)",
    },
    "samsungtv_smart": {
        "label": "Samsung Smart TV",
        "method": "media_player.play_media (browser / url)",
    },
    "samsungtv_tizen": {
        "label": "Samsung Smart TV",
        "method": "media_player.play_media (browser / url)",
    },
}

DISCONNECTED_STATES = frozenset({"unavailable", "unknown"})
STANDBY_STATES = frozenset({"off", "standby"})


class TVLaunchError(Exception):
    """Raised when opening the QuizRooms TV dashboard on a media player fails."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _get_entity_registry(hass: Any) -> Any | None:
    if hass is None:
        return None
    try:
        return er.async_get(hass)
    except Exception:
        return None


def discover_supported_tv_players(hass: Any) -> list[dict[str, Any]]:
    """Return currently connected, supported Smart TV media_player entities."""
    ent_reg = _get_entity_registry(hass)
    states = getattr(hass, "states", None)
    if ent_reg is None or states is None:
        return []

    targets: list[dict[str, Any]] = []
    for entry in ent_reg.entities.values():
        if getattr(entry, "domain", None) != "media_player":
            continue
        if getattr(entry, "disabled_by", None) is not None:
            continue
        platform = str(getattr(entry, "platform", "") or "").lower()
        if platform not in SUPPORTED_TV_PLATFORMS:
            continue

        entity_id = str(entry.entity_id)
        state_obj = states.get(entity_id)
        if state_obj is None:
            continue
        raw_state = str(getattr(state_obj, "state", "") or "").lower()
        if not raw_state or raw_state in DISCONNECTED_STATES:
            continue

        attrs = getattr(state_obj, "attributes", None) or {}
        friendly_name = (
            attrs.get("friendly_name")
            or getattr(entry, "name", None)
            or getattr(entry, "original_name", None)
            or entity_id
        )
        meta = SUPPORTED_TV_PLATFORMS[platform]
        is_on = raw_state not in STANDBY_STATES
        targets.append(
            {
                "entity_id": entity_id,
                "name": str(friendly_name),
                "platform": platform,
                "platform_label": meta["label"],
                "state": raw_state,
                "is_on": is_on,
                "launch_method": meta["method"],
            }
        )

    # Sort active/powered-on TVs first, then alphabetically by name
    targets.sort(key=lambda item: (not item["is_on"], item["name"].casefold(), item["entity_id"]))
    return targets


def _find_sibling_remote_entity(ent_reg: Any, entry: Any) -> str | None:
    """Find a sibling `remote.*` entity on the same device (used by androidtv_remote)."""
    device_id = getattr(entry, "device_id", None)
    if not device_id or ent_reg is None:
        return None
    for other in ent_reg.entities.values():
        if (
            getattr(other, "device_id", None) == device_id
            and getattr(other, "domain", None) == "remote"
            and getattr(other, "disabled_by", None) is None
        ):
            return str(other.entity_id)
    return None


async def async_open_tv_dashboard_on_player(
    hass: Any,
    entity_id: str,
    tv_url: str,
) -> dict[str, Any]:
    """Open the QuizRooms TV dashboard URL on a connected supported TV media player."""
    clean_entity_id = (entity_id or "").strip()
    clean_url = (tv_url or "").strip()
    if not clean_entity_id or not clean_entity_id.startswith("media_player."):
        raise TVLaunchError("invalid_entity", "A valid media_player entity_id is required.", status_code=400)
    if not clean_url.startswith(("http://", "https://")):
        raise TVLaunchError("invalid_url", "TV dashboard URL must start with http:// or https://.", status_code=400)

    ent_reg = _get_entity_registry(hass)
    if ent_reg is None:
        raise TVLaunchError("unavailable", "Home Assistant entity registry is not available.", status_code=503)

    entry = ent_reg.async_get(clean_entity_id) if hasattr(ent_reg, "async_get") else ent_reg.entities.get(clean_entity_id)
    if entry is None:
        raise TVLaunchError("entity_not_found", f"Entity '{clean_entity_id}' was not found.", status_code=404)

    platform = str(getattr(entry, "platform", "") or "").lower()
    if platform not in SUPPORTED_TV_PLATFORMS:
        raise TVLaunchError(
            "unsupported_platform",
            f"'{clean_entity_id}' (platform '{platform or 'unknown'}') is not a supported Smart TV browser target.",
            status_code=400,
        )

    states = getattr(hass, "states", None)
    state_obj = states.get(clean_entity_id) if states is not None else None
    raw_state = str(getattr(state_obj, "state", "") or "").lower() if state_obj is not None else ""
    if not raw_state or raw_state in DISCONNECTED_STATES:
        raise TVLaunchError(
            "tv_disconnected",
            f"TV '{clean_entity_id}' is currently unavailable or disconnected.",
            status_code=409,
        )

    attrs = getattr(state_obj, "attributes", None) or {}
    friendly_name = str(attrs.get("friendly_name") or getattr(entry, "name", None) or clean_entity_id)
    services = hass.services

    try:
        if platform == "webostv":
            await services.async_call(
                "webostv",
                "command",
                {
                    "entity_id": clean_entity_id,
                    "command": "system.launcher/open",
                    "payload": {"target": clean_url},
                },
                blocking=True,
            )
            return {
                "entity_id": clean_entity_id,
                "name": friendly_name,
                "platform": platform,
                "url": clean_url,
                "method": "webostv.command",
                "message": f"Opened QuizRooms TV dashboard on {friendly_name} (LG webOS).",
            }

        if platform == "androidtv":
            adb_cmd = f"am start -a android.intent.action.VIEW -d {shlex.quote(clean_url)}"
            await services.async_call(
                "androidtv",
                "adb_command",
                {
                    "entity_id": clean_entity_id,
                    "command": adb_cmd,
                },
                blocking=True,
            )
            return {
                "entity_id": clean_entity_id,
                "name": friendly_name,
                "platform": platform,
                "url": clean_url,
                "method": "androidtv.adb_command",
                "message": f"Sent browser launch command to {friendly_name} (Android TV ADB).",
            }

        if platform == "androidtv_remote":
            try:
                await services.async_call(
                    "media_player",
                    "play_media",
                    {
                        "entity_id": clean_entity_id,
                        "media_content_type": "url",
                        "media_content_id": clean_url,
                    },
                    blocking=True,
                )
                return {
                    "entity_id": clean_entity_id,
                    "name": friendly_name,
                    "platform": platform,
                    "url": clean_url,
                    "method": "media_player.play_media",
                    "message": f"Launched QuizRooms TV URL on {friendly_name} (Android TV / Google TV).",
                }
            except Exception as first_err:
                remote_eid = _find_sibling_remote_entity(ent_reg, entry)
                if remote_eid:
                    await services.async_call(
                        "remote",
                        "turn_on",
                        {
                            "entity_id": remote_eid,
                            "activity": clean_url,
                        },
                        blocking=True,
                    )
                    return {
                        "entity_id": clean_entity_id,
                        "name": friendly_name,
                        "platform": platform,
                        "url": clean_url,
                        "method": "remote.turn_on",
                        "message": f"Launched QuizRooms TV URL via {remote_eid} on {friendly_name}.",
                    }
                raise first_err

        if platform in ("samsungtv_smart", "samsungtv_tizen"):
            # Custom Samsung Smart TV integrations support `browser` (or `url`) content type
            for content_type in ("browser", "url"):
                try:
                    await services.async_call(
                        "media_player",
                        "play_media",
                        {
                            "entity_id": clean_entity_id,
                            "media_content_type": content_type,
                            "media_content_id": clean_url,
                        },
                        blocking=True,
                    )
                    return {
                        "entity_id": clean_entity_id,
                        "name": friendly_name,
                        "platform": platform,
                        "url": clean_url,
                        "method": f"media_player.play_media:{content_type}",
                        "message": f"Opened QuizRooms TV dashboard on {friendly_name} (Samsung Smart TV).",
                    }
                except Exception:
                    continue

            # Final fallback: launch Tizen Internet Browser app
            await services.async_call(
                "media_player",
                "play_media",
                {
                    "entity_id": clean_entity_id,
                    "media_content_type": "app",
                    "media_content_id": "org.tizen.browser",
                },
                blocking=True,
            )
            return {
                "entity_id": clean_entity_id,
                "name": friendly_name,
                "platform": platform,
                "url": clean_url,
                "method": "media_player.play_media:app",
                "message": (
                    f"Opened Samsung Internet Browser on {friendly_name}. "
                    f"If it did not auto-navigate, open or bookmark {clean_url}."
                ),
            }

        if platform == "samsungtv":
            try:
                await services.async_call(
                    "media_player",
                    "play_media",
                    {
                        "entity_id": clean_entity_id,
                        "media_content_type": "url",
                        "media_content_id": clean_url,
                    },
                    blocking=True,
                )
                return {
                    "entity_id": clean_entity_id,
                    "name": friendly_name,
                    "platform": platform,
                    "url": clean_url,
                    "method": "media_player.play_media:url",
                    "message": f"Sent QuizRooms TV URL to {friendly_name} (Samsung Smart TV).",
                }
            except Exception:
                await services.async_call(
                    "media_player",
                    "play_media",
                    {
                        "entity_id": clean_entity_id,
                        "media_content_type": "app",
                        "media_content_id": "org.tizen.browser",
                    },
                    blocking=True,
                )
                return {
                    "entity_id": clean_entity_id,
                    "name": friendly_name,
                    "platform": platform,
                    "url": clean_url,
                    "method": "media_player.play_media:app",
                    "message": (
                        f"Opened Samsung Internet Browser on {friendly_name} "
                        f"(set {clean_url} as homepage if your Tizen model blocks direct URLs)."
                    ),
                }
    except TVLaunchError:
        raise
    except Exception as err:
        _LOGGER.warning("Failed opening QuizRooms TV dashboard on %s (%s): %s", clean_entity_id, platform, err)
        raise TVLaunchError(
            "launch_failed",
            f"Could not open QuizRooms TV dashboard on {friendly_name}: {err}",
            status_code=502,
        ) from err

    raise TVLaunchError("unsupported_platform", f"Unsupported TV platform '{platform}'.", status_code=400)
