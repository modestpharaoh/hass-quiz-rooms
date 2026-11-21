"""Number entity (number.quizrooms_max_rooms) for controlling max concurrent rooms."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DEFAULT_MAX_ROOMS,
    DOMAIN,
    MAX_MAX_ROOMS,
    MIN_MAX_ROOMS,
)
from .coordinator import QuizRoomsCoordinator, build_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up QuizRooms number entities."""
    coordinator: QuizRoomsCoordinator = hass.data[DOMAIN]["coordinator"]
    async_add_entities([QuizRoomsMaxRoomsNumber(coordinator, entry)])


class QuizRoomsMaxRoomsNumber(NumberEntity, RestoreEntity):
    """Configurable max concurrent rooms (graceful mid-game lowering)."""

    _attr_has_entity_name = True
    _attr_translation_key = "max_rooms"
    _attr_icon = "mdi:door-open"
    _attr_native_min_value = float(MIN_MAX_ROOMS)
    _attr_native_max_value = float(MAX_MAX_ROOMS)
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: QuizRoomsCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_max_rooms"
        self._attr_device_info = build_device_info(entry.entry_id)
        self.entity_id = "number.quizrooms_max_rooms"
        self._unsub = None

    @property
    def native_value(self) -> float:
        return float(self._coordinator.engine.max_rooms)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in ("unknown", "unavailable", ""):
            try:
                restored = int(float(last_state.state))
                self._coordinator.engine.set_max_rooms(restored)
            except (TypeError, ValueError):
                self._coordinator.engine.set_max_rooms(DEFAULT_MAX_ROOMS)
        self._unsub = self._coordinator.add_entity_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def async_set_native_value(self, value: float) -> None:
        """Update max rooms without terminating active rooms."""
        await self._coordinator.async_set_max_rooms(int(round(value)))
        self.async_write_ha_state()
