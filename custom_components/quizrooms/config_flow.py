"""ConfigFlow and OptionsFlow for QuizRooms."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

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


def _validate_network_options(user_input: dict[str, Any]) -> dict[str, str]:
    """Validate CIDR list and optional custom LAN base URL."""
    errors: dict[str, str] = {}
    subnets_raw = str(user_input.get(CONF_ALLOWED_SUBNETS, "") or "").strip()
    if subnets_raw:
        try:
            parse_cidr_list(subnets_raw)
        except ValueError:
            errors[CONF_ALLOWED_SUBNETS] = "invalid_subnet"

    base_url = str(user_input.get(CONF_CUSTOM_LAN_BASE_URL, "") or "").strip()
    if base_url and not is_safe_http_url(base_url):
        errors[CONF_CUSTOM_LAN_BASE_URL] = "invalid_url"

    return errors


def _build_options_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_ALLOW_EXTERNAL_ACCESS,
                default=defaults.get(CONF_ALLOW_EXTERNAL_ACCESS, DEFAULT_ALLOW_EXTERNAL_ACCESS),
            ): bool,
            vol.Optional(
                CONF_ALLOWED_SUBNETS,
                default=defaults.get(CONF_ALLOWED_SUBNETS, DEFAULT_ALLOWED_SUBNETS),
            ): str,
            vol.Optional(
                CONF_CUSTOM_LAN_BASE_URL,
                default=defaults.get(CONF_CUSTOM_LAN_BASE_URL, DEFAULT_CUSTOM_LAN_BASE_URL),
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
            errors = _validate_network_options(user_input)
            if not errors:
                return self.async_create_entry(
                    title="QuizRooms",
                    data={},
                    options={
                        CONF_ALLOW_EXTERNAL_ACCESS: bool(
                            user_input.get(CONF_ALLOW_EXTERNAL_ACCESS, DEFAULT_ALLOW_EXTERNAL_ACCESS)
                        ),
                        CONF_ALLOWED_SUBNETS: str(
                            user_input.get(CONF_ALLOWED_SUBNETS, DEFAULT_ALLOWED_SUBNETS) or ""
                        ).strip(),
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
            CONF_ALLOWED_SUBNETS: options.get(
                CONF_ALLOWED_SUBNETS, DEFAULT_ALLOWED_SUBNETS
            ),
            CONF_CUSTOM_LAN_BASE_URL: options.get(
                CONF_CUSTOM_LAN_BASE_URL, DEFAULT_CUSTOM_LAN_BASE_URL
            ),
        }

        if user_input is not None:
            errors = _validate_network_options(user_input)
            if not errors:
                new_options = {
                    CONF_ALLOW_EXTERNAL_ACCESS: bool(
                        user_input.get(CONF_ALLOW_EXTERNAL_ACCESS, DEFAULT_ALLOW_EXTERNAL_ACCESS)
                    ),
                    CONF_ALLOWED_SUBNETS: str(
                        user_input.get(CONF_ALLOWED_SUBNETS, DEFAULT_ALLOWED_SUBNETS) or ""
                    ).strip(),
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
            current.update(user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_build_options_schema(current),
            errors=errors,
        )
