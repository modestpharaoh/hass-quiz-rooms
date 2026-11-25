"""ConfigFlow and OptionsFlow for QuizRooms."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ALLOW_EXTERNAL_ACCESS,
    CONF_ALLOWED_SUBNETS,
    CONF_CUSTOM_LAN_BASE_URL,
    CONF_STAGE_EFFECTS,
    DEFAULT_ALLOW_EXTERNAL_ACCESS,
    DEFAULT_ALLOWED_SUBNETS,
    DEFAULT_CUSTOM_LAN_BASE_URL,
    DOMAIN,
)
from .network_guard import parse_cidr_list
from .tv_launcher import is_safe_http_url


def _coerce_subnet_tokens(raw: Any) -> list[str]:
    """Convert stored or submitted allowed_subnets (str or list[str]) to a deduplicated token list."""
    if not raw:
        return []
    parts: list[str] = []
    if isinstance(raw, str):
        raw_items = [raw]
    elif isinstance(raw, (list, tuple, set)):
        raw_items = [str(item) for item in raw if item is not None]
    else:
        raw_items = [str(raw)]

    seen: set[str] = set()
    for item in raw_items:
        for chunk in item.replace("\n", ",").split(","):
            token = chunk.strip()
            if token and token not in seen:
                seen.add(token)
                parts.append(token)
    return parts


def _validate_and_normalize_subnets(raw: Any) -> tuple[list[str], str | None]:
    """Validate every CIDR entry and return canonical deduplicated strings or an error key."""
    tokens = _coerce_subnet_tokens(raw)
    if not tokens:
        return [], None
    try:
        networks = parse_cidr_list(tokens)
    except ValueError:
        return [], "invalid_subnet"

    normalized: list[str] = []
    seen: set[str] = set()
    for net in networks:
        cidr = str(net)
        if cidr not in seen:
            seen.add(cidr)
            normalized.append(cidr)
    return normalized, None


def _validate_network_options(user_input: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Validate CIDR list and optional custom LAN base URL, returning (errors, normalized_subnets)."""
    errors: dict[str, str] = {}
    normalized_subnets, subnet_err = _validate_and_normalize_subnets(
        user_input.get(CONF_ALLOWED_SUBNETS)
    )
    if subnet_err:
        errors[CONF_ALLOWED_SUBNETS] = subnet_err

    base_url = str(user_input.get(CONF_CUSTOM_LAN_BASE_URL, "") or "").strip()
    if base_url and not is_safe_http_url(base_url):
        errors[CONF_CUSTOM_LAN_BASE_URL] = "invalid_url"

    return errors, normalized_subnets


def _build_options_schema(defaults: dict[str, Any]) -> vol.Schema:
    # IMPORTANT:
    # 1. Use SelectSelector(multiple=True, custom_value=True) for allowed_subnets so each
    #    CIDR renders as an individual removable chip with an 'x' button in Home Assistant.
    # 2. Use description={"suggested_value": ...} (NOT default=...) for optional fields so
    #    Home Assistant / voluptuous does not silently re-inject previous values when cleared.
    subnet_list = _coerce_subnet_tokens(
        defaults.get(CONF_ALLOWED_SUBNETS, DEFAULT_ALLOWED_SUBNETS)
    )
    return vol.Schema(
        {
            vol.Optional(
                CONF_ALLOW_EXTERNAL_ACCESS,
                default=defaults.get(CONF_ALLOW_EXTERNAL_ACCESS, DEFAULT_ALLOW_EXTERNAL_ACCESS),
            ): bool,
            vol.Optional(
                CONF_ALLOWED_SUBNETS,
                description={"suggested_value": subnet_list},
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=subnet_list,
                    multiple=True,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_CUSTOM_LAN_BASE_URL,
                description={
                    "suggested_value": defaults.get(
                        CONF_CUSTOM_LAN_BASE_URL, DEFAULT_CUSTOM_LAN_BASE_URL
                    )
                },
            ): str,
        }
    )


class QuizRoomsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Zero-config setup flow for QuizRooms."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            errors, normalized_subnets = _validate_network_options(user_input)
            if not errors:
                return self.async_create_entry(
                    title="QuizRooms",
                    data={},
                    options={
                        CONF_ALLOW_EXTERNAL_ACCESS: bool(
                            user_input.get(CONF_ALLOW_EXTERNAL_ACCESS, DEFAULT_ALLOW_EXTERNAL_ACCESS)
                        ),
                        CONF_ALLOWED_SUBNETS: normalized_subnets,
                        CONF_CUSTOM_LAN_BASE_URL: str(
                            user_input.get(CONF_CUSTOM_LAN_BASE_URL, DEFAULT_CUSTOM_LAN_BASE_URL) or ""
                        ).strip().rstrip("/"),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_options_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        if hasattr(config_entries.OptionsFlow, "config_entry"):
            return QuizRoomsOptionsFlow()
        return QuizRoomsOptionsFlow(config_entry)


class QuizRoomsOptionsFlow(config_entries.OptionsFlow):
    """Runtime options flow (applied dynamically without restart)."""

    def __init__(self, config_entry: config_entries.ConfigEntry | None = None) -> None:
        if config_entry is not None:
            self._fallback_config_entry = config_entry

    @property
    def _entry(self) -> config_entries.ConfigEntry:
        if hasattr(self, "_fallback_config_entry"):
            return self._fallback_config_entry
        return self.config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        options = self._entry.options
        current = {
            CONF_ALLOW_EXTERNAL_ACCESS: options.get(
                CONF_ALLOW_EXTERNAL_ACCESS, DEFAULT_ALLOW_EXTERNAL_ACCESS
            ),
            CONF_ALLOWED_SUBNETS: _coerce_subnet_tokens(
                options.get(CONF_ALLOWED_SUBNETS, DEFAULT_ALLOWED_SUBNETS)
            ),
            CONF_CUSTOM_LAN_BASE_URL: options.get(
                CONF_CUSTOM_LAN_BASE_URL, DEFAULT_CUSTOM_LAN_BASE_URL
            ),
        }

        if user_input is not None:
            errors, normalized_subnets = _validate_network_options(user_input)
            if not errors:
                new_options: dict[str, Any] = {
                    CONF_ALLOW_EXTERNAL_ACCESS: bool(
                        user_input.get(CONF_ALLOW_EXTERNAL_ACCESS, DEFAULT_ALLOW_EXTERNAL_ACCESS)
                    ),
                    CONF_ALLOWED_SUBNETS: normalized_subnets,
                    CONF_CUSTOM_LAN_BASE_URL: str(
                        user_input.get(CONF_CUSTOM_LAN_BASE_URL, DEFAULT_CUSTOM_LAN_BASE_URL) or ""
                    ).strip().rstrip("/"),
                }
                if CONF_STAGE_EFFECTS in options:
                    new_options[CONF_STAGE_EFFECTS] = options[CONF_STAGE_EFFECTS]
                return self.async_create_entry(
                    title="",
                    data=new_options,
                )
            current = {
                CONF_ALLOW_EXTERNAL_ACCESS: bool(
                    user_input.get(CONF_ALLOW_EXTERNAL_ACCESS, DEFAULT_ALLOW_EXTERNAL_ACCESS)
                ),
                CONF_ALLOWED_SUBNETS: _coerce_subnet_tokens(
                    user_input.get(CONF_ALLOWED_SUBNETS)
                ),
                CONF_CUSTOM_LAN_BASE_URL: str(
                    user_input.get(CONF_CUSTOM_LAN_BASE_URL, DEFAULT_CUSTOM_LAN_BASE_URL) or ""
                ),
            }

        return self.async_show_form(
            step_id="init",
            data_schema=_build_options_schema(current),
            errors=errors,
        )
