"""Read the area mapping a vacuum keeps in its entity registry options.

The mapping belongs to the vacuum domain (vacuum entity → settings → map
areas to segments); this integration only reads it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from homeassistant.components.vacuum.const import DATA_COMPONENT
from homeassistant.components.vacuum.const import DOMAIN as VACUUM_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

# Names a vacuum reports for segments nobody has named yet: "1", "Room 3",
# "Segment 12", "Raum 2" ... (Valetudo reports the bare segment id).
_PLACEHOLDER_NAME = re.compile(
    r"^\s*(room|raum|zimmer|segment|area|bereich)?\s*[#_-]?\s*\d+\s*$", re.IGNORECASE
)


def area_mapping(hass: HomeAssistant, entity_id: str) -> dict[str, list[str]]:
    """Return area id → segment ids for the vacuum ({} if not mapped)."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return {}
    options: Mapping[str, Any] = entry.options.get(VACUUM_DOMAIN, {})
    mapping = options.get("area_mapping")
    return dict(mapping) if isinstance(mapping, dict) else {}


def mapped_areas(hass: HomeAssistant, entity_id: str) -> list[str]:
    """Return the areas that have at least one segment, in mapping order."""
    mapping = area_mapping(hass, entity_id)
    return [area for area, segments in mapping.items() if segments]


async def async_unnamed_segments(hass: HomeAssistant, entity_id: str) -> list[str]:
    """Return the segment labels if all segments only carry placeholder names.

    Then the mapping dialog of Home Assistant shows nothing but numbers, and
    the rooms have to be named in the vacuum's own app first. Returns [] when
    the segments have real names (or cannot be read).
    """
    component = hass.data.get(DATA_COMPONENT)
    entity = component.get_entity(entity_id) if component else None
    if entity is None:
        return []
    try:
        segments = await entity.async_get_segments()
    except Exception:
        _LOGGER.debug("Could not read the segments of %s", entity_id, exc_info=True)
        return []
    if not all(
        not segment.name or _PLACEHOLDER_NAME.match(segment.name)
        for segment in segments
    ):
        return []
    return [segment.name or segment.id for segment in segments]
