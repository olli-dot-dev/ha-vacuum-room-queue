"""Tests for reading the area mapping and segment names."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.components.vacuum import Segment
from homeassistant.components.vacuum.const import DATA_COMPONENT
from homeassistant.core import HomeAssistant

from custom_components.vacuum_room_queue.mapping import async_unnamed_segments

from .conftest import VACUUM


class _Entity:
    def __init__(self, names: list[str] | Exception) -> None:
        self._names = names

    async def async_get_segments(self) -> list[Segment]:
        if isinstance(self._names, Exception):
            raise self._names
        return [Segment(id=str(i), name=n) for i, n in enumerate(self._names)]


def _component(entity: _Entity | None) -> SimpleNamespace:
    return SimpleNamespace(get_entity=lambda _entity_id: entity)


@pytest.mark.parametrize(
    ("names", "unnamed"),
    [
        (["1", "2", "12"], ["1", "2", "12"]),
        # A segment without any name is shown by its id.
        (["Room 1", "Raum 2", "segment_3", ""], ["Room 1", "Raum 2", "segment_3", "3"]),
        (["1", "Küche"], []),
        (["Kitchen", "Living room"], []),
        ([], []),
        (RuntimeError("offline"), []),
    ],
)
async def test_unnamed_segments(
    hass: HomeAssistant, names: list[str] | Exception, unnamed: list[str]
) -> None:
    hass.data[DATA_COMPONENT] = _component(_Entity(names))  # type: ignore[assignment]
    assert await async_unnamed_segments(hass, VACUUM) == unnamed


async def test_unnamed_segments_without_entity(hass: HomeAssistant) -> None:
    assert await async_unnamed_segments(hass, VACUUM) == []
    hass.data[DATA_COMPONENT] = _component(None)  # type: ignore[assignment]
    assert await async_unnamed_segments(hass, VACUUM) == []
