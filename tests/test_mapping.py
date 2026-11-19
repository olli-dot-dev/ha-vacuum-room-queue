"""Tests for reading the area mapping and segment names."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.components.vacuum import Segment
from homeassistant.components.vacuum.const import DATA_COMPONENT
from homeassistant.core import HomeAssistant

from custom_components.vacuum_room_queue.mapping import async_segments_unnamed

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
        (["1", "2", "12"], True),
        (["Room 1", "Raum 2", "segment_3", ""], True),
        (["1", "Küche"], False),
        (["Kitchen", "Living room"], False),
        ([], False),
        (RuntimeError("offline"), False),
    ],
)
async def test_segments_unnamed(
    hass: HomeAssistant, names: list[str] | Exception, unnamed: bool
) -> None:
    hass.data[DATA_COMPONENT] = _component(_Entity(names))  # type: ignore[assignment]
    assert await async_segments_unnamed(hass, VACUUM) is unnamed


async def test_segments_unnamed_without_entity(hass: HomeAssistant) -> None:
    assert await async_segments_unnamed(hass, VACUUM) is False
    hass.data[DATA_COMPONENT] = _component(None)  # type: ignore[assignment]
    assert await async_segments_unnamed(hass, VACUUM) is False
