"""A vacuum that 'cleans' a segment for CLEAN_SECONDS, then drives home.

YAML:  vacuum:
         - platform: fake_vacuum
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.vacuum import (
    Segment,
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

CLEAN_SECONDS = 20
RETURN_SECONDS = 8
SEGMENTS = [
    Segment("1", "Küche"),
    Segment("2", "Wohnzimmer"),
    Segment("3", "Flur"),
    Segment("4", "Bad"),
    Segment("5", "Schlafzimmer"),
]


async def async_setup_platform(hass: HomeAssistant, config, add_entities, discovery_info=None) -> None:
    add_entities([FakeVacuum()])


class FakeVacuum(StateVacuumEntity):
    _attr_name = "Fake Robi"
    _attr_unique_id = "fake_vacuum_robi"
    _attr_supported_features = (
        VacuumEntityFeature.CLEAN_AREA
        | VacuumEntityFeature.START
        | VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.STATE
    )
    _attr_activity = VacuumActivity.DOCKED

    def __init__(self) -> None:
        self._timer = None
        self.cleaning_segments: list[str] = []
        self._remaining: float = 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"cleaning_segments": self.cleaning_segments}

    def _cancel(self) -> None:
        if self._timer:
            self._timer()
            self._timer = None

    def _set(self, activity: VacuumActivity) -> None:
        self._attr_activity = activity
        self.async_write_ha_state()

    def _schedule(self, seconds: float, action) -> None:
        self._cancel()
        self._timer = async_call_later(self.hass, seconds, action)

    @callback
    def _finish_cleaning(self, _now=None) -> None:
        # Schedule before writing the state: a listener may send the next
        # room from inside the state write.
        self.cleaning_segments = []
        self._schedule(RETURN_SECONDS, self._docked)
        self._set(VacuumActivity.RETURNING)

    @callback
    def _docked(self, _now=None) -> None:
        self._timer = None
        self._set(VacuumActivity.DOCKED)

    async def async_get_segments(self) -> list[Segment]:
        return SEGMENTS

    async def async_clean_segments(self, segment_ids: list[str], **kwargs: Any) -> None:
        self.cleaning_segments = list(segment_ids)
        self._set(VacuumActivity.CLEANING)
        self._schedule(CLEAN_SECONDS * len(segment_ids), self._finish_cleaning)

    async def async_start(self) -> None:
        if self._attr_activity == VacuumActivity.PAUSED and self.cleaning_segments:
            self._set(VacuumActivity.CLEANING)
            self._schedule(self._remaining or CLEAN_SECONDS, self._finish_cleaning)

    async def async_pause(self) -> None:
        if self._attr_activity == VacuumActivity.CLEANING:
            self._remaining = CLEAN_SECONDS / 2
            self._cancel()
            self._set(VacuumActivity.PAUSED)

    async def async_stop(self, **kwargs: Any) -> None:
        self._cancel()
        self.cleaning_segments = []
        self._set(VacuumActivity.IDLE)

    async def async_return_to_base(self, **kwargs: Any) -> None:
        self.cleaning_segments = []
        self._schedule(RETURN_SECONDS, self._docked)
        self._set(VacuumActivity.RETURNING)
