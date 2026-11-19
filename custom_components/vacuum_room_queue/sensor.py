"""Sensor showing the room queue; the dashboard card reads its attributes."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RoomQueueConfigEntry
from .const import QueueStatus
from .manager import RoomQueueManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RoomQueueConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the queue sensor."""
    async_add_entities([RoomQueueSensor(hass, entry.runtime_data)])


class RoomQueueSensor(SensorEntity):
    """Status of the queue, with the rooms as attributes."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "queue"
    _attr_should_poll = False
    # The room list is configuration, not history.
    _unrecorded_attributes = frozenset({"rooms", "vacuum_entity_id"})

    def __init__(self, hass: HomeAssistant, manager: RoomQueueManager) -> None:
        """Attach the sensor to the device of the vacuum, if there is one."""
        self._manager = manager
        self._attr_options = [status.value for status in QueueStatus]
        self._attr_unique_id = f"{manager.entry.entry_id}_queue"
        # Shown on the device page of the vacuum, the way helpers like
        # derivative do it (a device belongs to one config entry only).
        self._attr_has_entity_name = True
        device = async_entity_id_to_device(hass, manager.vacuum_entity_id)
        if device is not None:
            self.device_entry = device
        else:
            # No device to put its name in front, so the vacuum's name goes
            # into the translated name ("{vacuum} room queue"). HA then builds
            # name and entity ID itself, with its own language rules.
            state = hass.states.get(manager.vacuum_entity_id)
            self._attr_translation_key = "queue_without_device"
            self._attr_translation_placeholders = {
                "vacuum": state.name if state is not None else manager.entry.title
            }

    async def async_added_to_hass(self) -> None:
        """Follow the manager."""
        self.async_on_remove(
            self._manager.async_add_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self) -> str:
        """Return the queue status."""
        return self._manager.status.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Everything the card needs."""
        manager = self._manager
        return {
            "vacuum_entity_id": manager.vacuum_entity_id,
            "current_area": manager.current,
            "queue": list(manager.queue),
            "done": list(manager.done),
            "error": manager.error.value if manager.error else None,
            "rooms": [
                {"id": room.id, "name": room.name} for room in manager.available_rooms
            ],
            "rooms_hint": manager.rooms_hint,
            "history": manager.history,
            "last_cleaned": dict(manager.last_cleaned),
        }
