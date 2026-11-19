"""Fixtures for Vacuum Room Queue tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from homeassistant.components.vacuum.const import VacuumEntityFeature
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.vacuum_room_queue.const import (
    CONF_AREAS,
    CONF_METHOD,
    CONF_VACUUM,
    DOMAIN,
    METHOD_CLEAN_AREA,
)

VACUUM = "vacuum.robot"
SENSOR = "sensor.robot_room_queue"
FEATURES = (
    VacuumEntityFeature.CLEAN_AREA
    | VacuumEntityFeature.RETURN_HOME
    | VacuumEntityFeature.PAUSE
    | VacuumEntityFeature.START
    | VacuumEntityFeature.STATE
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


@dataclass
class Vacuum:
    """A fake vacuum: a state plus recorded service calls."""

    hass: HomeAssistant
    calls: dict[str, list[ServiceCall]]

    def set(self, activity: str, features: int = FEATURES) -> None:
        self.hass.states.async_set(
            VACUUM, activity, {"supported_features": features, "friendly_name": "Robot"}
        )

    def cleaned(self) -> list[str]:
        return [c.data["cleaning_area_id"][0] for c in self.calls["clean_area"]]


@pytest.fixture
async def areas(hass: HomeAssistant) -> dict[str, str]:
    """Four rooms, by name."""
    registry = ar.async_get(hass)
    return {
        name: registry.async_create(name).id
        for name in ("Kitchen", "Living", "Hall", "Bath")
    }


@pytest.fixture
async def vacuum(hass: HomeAssistant, areas: dict[str, str]) -> Vacuum:
    """A vacuum that maps Kitchen, Living and Hall to segments."""
    assert await async_setup_component(hass, "vacuum", {})
    registry = er.async_get(hass)
    registry.async_get_or_create("vacuum", "test", "robot", suggested_object_id="robot")
    registry.async_update_entity_options(
        VACUUM,
        "vacuum",
        {
            "area_mapping": {
                areas["Kitchen"]: ["1"],
                areas["Living"]: ["2"],
                areas["Hall"]: ["3"],
                areas["Bath"]: [],
            },
            "last_seen_segments": [],
        },
    )
    calls = {
        service: async_mock_service(hass, "vacuum", service)
        for service in ("clean_area", "start", "pause", "return_to_base", "stop")
    }
    fake = Vacuum(hass, calls)
    fake.set("docked")
    return fake


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry for the vacuum using native area cleaning."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Robot",
        data={CONF_VACUUM: VACUUM},
        options={CONF_METHOD: METHOD_CLEAN_AREA, CONF_AREAS: []},
    )
    config_entry.add_to_hass(hass)
    return config_entry


@pytest.fixture
async def loaded(
    hass: HomeAssistant, vacuum: Vacuum, entry: MockConfigEntry
) -> MockConfigEntry:
    """The integration, set up."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
