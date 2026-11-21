"""Sensor entities for active QuizRooms games and players."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import QuizRoomsCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up QuizRooms sensor entities."""
    coordinator: QuizRoomsCoordinator = hass.data[DOMAIN]["coordinator"]
    async_add_entities(
        [
            QuizRoomsActiveGamesSensor(coordinator, entry),
            QuizRoomsActivePlayersSensor(coordinator, entry),
        ]
    )


class _QuizRoomsBaseSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: QuizRoomsCoordinator) -> None:
        self._coordinator = coordinator
        self._unsub = None

    async def async_added_to_hass(self) -> None:
        self._unsub = self._coordinator.add_entity_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None


class QuizRoomsActiveGamesSensor(_QuizRoomsBaseSensor):
    """Reports count of active rooms plus room codes and statuses in attributes."""

    _attr_translation_key = "active_games"
    _attr_icon = "mdi:gamepad-variant"

    def __init__(self, coordinator: QuizRoomsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_active_games"
        self.entity_id = "sensor.quizrooms_active_games"

    @property
    def native_value(self) -> int:
        return self._coordinator.engine.active_room_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rooms = self._coordinator.engine.serialize_admin_rooms()
        return {
            "max_rooms": self._coordinator.engine.max_rooms,
            "room_codes": [r["code"] for r in rooms],
            "rooms": rooms,
        }


class QuizRoomsActivePlayersSensor(_QuizRoomsBaseSensor):
    """Reports total connected players across all active QuizRooms rooms."""

    _attr_translation_key = "active_players"
    _attr_icon = "mdi:account-group"

    def __init__(self, coordinator: QuizRoomsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_active_players"
        self.entity_id = "sensor.quizrooms_active_players"

    @property
    def native_value(self) -> int:
        return self._coordinator.engine.active_player_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        by_room = {
            code: sum(1 for p in room.players.values() if p.connected)
            for code, room in self._coordinator.engine.rooms.items()
        }
        return {"by_room": by_room}
