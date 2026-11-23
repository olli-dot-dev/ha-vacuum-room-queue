"""Tests for the config and options flow."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vacuum_room_queue import config_flow
from custom_components.vacuum_room_queue.const import (
    CONF_AREAS,
    CONF_METHOD,
    CONF_SCRIPT,
    CONF_START_TIMEOUT,
    CONF_VACUUM,
    DOMAIN,
    METHOD_CLEAN_AREA,
    METHOD_SCRIPT,
)

from .conftest import VACUUM, Vacuum


@pytest.mark.usefixtures("vacuum")
async def test_native_flow(hass: HomeAssistant, areas: dict[str, str]) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VACUUM: VACUUM}
    )
    # It can clean areas: no question, straight on to the rooms.
    assert result["step_id"] == "rooms_clean_area"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_AREAS: [areas["Hall"]]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Robot"
    assert result["description"] == "reload_browser"
    assert result["data"] == {CONF_VACUUM: VACUUM}
    assert result["options"] == {
        CONF_METHOD: METHOD_CLEAN_AREA,
        CONF_AREAS: [areas["Hall"]],
    }

    # A second queue for the same vacuum is refused.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VACUUM: VACUUM}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_script_flow(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    vacuum.set("docked", features=0)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VACUUM: VACUUM}
    )
    # It can't clean areas: straight on to the script.
    assert result["step_id"] == "rooms_script"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SCRIPT: "script.clean_room", CONF_AREAS: []}
    )
    assert result["errors"] == {CONF_AREAS: "no_areas"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SCRIPT: "script.clean_room", CONF_AREAS: [areas["Bath"]]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_SCRIPT] == "script.clean_room"


async def test_options_flow(
    hass: HomeAssistant, loaded: MockConfigEntry, areas: dict[str, str]
) -> None:
    result = await hass.config_entries.options.async_init(loaded.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_METHOD: METHOD_SCRIPT}
    )
    assert result["step_id"] == "rooms_script"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCRIPT: "script.x", CONF_AREAS: []}
    )
    assert result["errors"] == {CONF_AREAS: "no_areas"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCRIPT: "script.x", CONF_AREAS: [areas["Bath"]], CONF_START_TIMEOUT: 15},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert loaded.options == {
        CONF_METHOD: METHOD_SCRIPT,
        CONF_SCRIPT: "script.x",
        CONF_AREAS: [areas["Bath"]],
        CONF_START_TIMEOUT: 15,
    }
    # The entry was reloaded with the new rooms.
    assert loaded.runtime_data.configured_areas == [areas["Bath"]]


def _clear_mapping(hass: HomeAssistant) -> None:
    er.async_get(hass).async_update_entity_options(
        VACUUM, "vacuum", {"area_mapping": {}, "last_seen_segments": []}
    )


@pytest.mark.parametrize(
    ("unnamed", "step"), [(False, "mapping_missing"), (True, "mapping_unnamed")]
)
async def test_flow_explains_missing_mapping(
    hass: HomeAssistant,
    vacuum: Vacuum,
    areas: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    unnamed: bool,
    step: str,
) -> None:
    """No mapping: no empty entry, but a hint and a way to check again."""
    mapping = er.async_get(hass).async_get(VACUUM).options["vacuum"]
    _clear_mapping(hass)

    async def unnamed_segments(hass: HomeAssistant, entity_id: str) -> list[str]:
        return ["1", "2"] if unnamed else []

    monkeypatch.setattr(config_flow, "async_unnamed_segments", unnamed_segments)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VACUUM: VACUUM}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == step
    assert result["menu_options"] == ["recheck", "use_script"]
    assert result["description_placeholders"] == (
        {"vacuum": "Robot", "segments": "1, 2"} if unnamed else {"vacuum": "Robot"}
    )

    # Still not mapped: the hint stays.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "recheck"}
    )
    assert result["step_id"] == step

    # Mapped in the meantime: on to the rooms.
    er.async_get(hass).async_update_entity_options(VACUUM, "vacuum", mapping)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "recheck"}
    )
    assert result["step_id"] == "rooms_clean_area"


async def test_flow_missing_mapping_use_script(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    _clear_mapping(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VACUUM: VACUUM}
    )
    assert result["step_id"] == "mapping_missing"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "use_script"}
    )
    assert result["step_id"] == "rooms_script"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SCRIPT: "script.x", CONF_AREAS: [areas["Bath"]]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_METHOD] == METHOD_SCRIPT


def _offered(result: dict) -> list[tuple[str, str]]:
    selector = result["data_schema"].schema[CONF_AREAS]
    return [(o["value"], o["label"]) for o in selector.config["options"]]


@pytest.mark.usefixtures("vacuum")
async def test_flow_offers_only_the_robots_rooms(
    hass: HomeAssistant, areas: dict[str, str]
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VACUUM: VACUUM}
    )
    # Bath has no segment: not offered, not named.
    assert _offered(result) == [
        (areas["Kitchen"], "Kitchen"),
        (areas["Living"], "Living"),
        (areas["Hall"], "Hall"),
    ]
    assert result["description_placeholders"] == {
        "vacuum": "Robot",
        "rooms": "Kitchen, Living, Hall",
    }
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_AREAS: [areas["Hall"], areas["Bath"]]}
        )


async def test_options_flow_flags_room_no_longer_mapped(
    hass: HomeAssistant, loaded: MockConfigEntry, areas: dict[str, str]
) -> None:
    # Saved earlier, mapping changed since: still shown, so it can be removed.
    hass.config_entries.async_update_entry(
        loaded, options={**loaded.options, CONF_AREAS: [areas["Bath"]]}
    )
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(loaded.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_METHOD: METHOD_CLEAN_AREA}
    )
    assert (areas["Bath"], "Bath") in _offered(result)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_AREAS: [areas["Bath"]], CONF_START_TIMEOUT: 10}
    )
    assert result["errors"] == {CONF_AREAS: "unmapped_areas"}
    assert result["description_placeholders"]["areas"] == "Bath"


async def test_options_flow_explains_missing_mapping(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    _clear_mapping(hass)
    result = await hass.config_entries.options.async_init(loaded.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_METHOD: METHOD_CLEAN_AREA}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "mapping_missing"
