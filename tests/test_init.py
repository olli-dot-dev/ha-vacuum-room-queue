"""Tests for serving the card and attaching to the vacuum device."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import vacuum_room_queue
from custom_components.vacuum_room_queue.const import DOMAIN

from .conftest import VACUUM, Vacuum


def test_card_file_exists() -> None:
    assert vacuum_room_queue.CARD_PATH.is_file()


async def test_card_is_served(hass: HomeAssistant) -> None:
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    hass.config.components.add("frontend")
    with (
        patch.object(vacuum_room_queue, "add_extra_js_url") as add_url,
        patch.object(
            vacuum_room_queue, "_async_register_card_resource"
        ) as register_resource,
    ):
        assert await vacuum_room_queue.async_setup(hass, {})
    configs = hass.http.async_register_static_paths.call_args.args[0]
    assert [c.url_path for c in configs] == [vacuum_room_queue.CARD_URL]
    url = add_url.call_args.args[1]
    assert url.startswith(f"{vacuum_room_queue.CARD_URL}?v=")
    assert register_resource.call_args.args[1] == url


async def test_card_resource_is_created_and_updated(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "lovelace", {})
    from homeassistant.components.lovelace.const import LOVELACE_DATA  # noqa: PLC0415

    resources = hass.data[LOVELACE_DATA].resources
    await vacuum_room_queue._async_register_card_resource(hass, "/x?v=1")
    url = f"{vacuum_room_queue.CARD_URL}?v=1"
    await vacuum_room_queue._async_register_card_resource(hass, url)
    await vacuum_room_queue._async_register_card_resource(hass, url)
    items = [i for i in resources.async_items() if i["url"].startswith("/vacuum")]
    assert [i["url"] for i in items] == [url]
    await vacuum_room_queue._async_register_card_resource(
        hass, f"{vacuum_room_queue.CARD_URL}?v=2"
    )
    items = [i for i in resources.async_items() if i["url"].startswith("/vacuum")]
    assert [i["url"] for i in items] == [f"{vacuum_room_queue.CARD_URL}?v=2"]


async def test_card_not_served_without_frontend(hass: HomeAssistant) -> None:
    with patch.object(vacuum_room_queue, "add_extra_js_url") as add_url:
        assert await vacuum_room_queue.async_setup(hass, {})
    add_url.assert_not_called()


async def test_card_resource_without_lovelace(hass: HomeAssistant) -> None:
    # Nothing to do, nothing raised.
    await vacuum_room_queue._async_register_card_resource(hass, "/x")


async def test_sensor_joins_vacuum_device(
    hass: HomeAssistant, vacuum: Vacuum, entry: MockConfigEntry
) -> None:
    other = MockConfigEntry(domain="test")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("test", "robot")}, name="Robi"
    )
    er.async_get(hass).async_update_entity(VACUUM, device_id=device.id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sensors = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert [s.device_id for s in sensors] == [device.id]
    assert sensors[0].has_entity_name


@pytest.mark.parametrize(
    ("language", "entity_id", "name"),
    [
        ("en", "sensor.robot_room_queue", "Robot Room queue"),
        ("de", "sensor.robot_raumreihenfolge", "Robot Raumreihenfolge"),
        ("fr", "sensor.robot_file_des_pieces", "Robot File des pièces"),
        # Not in HA's NATIVE_ENTITY_IDS: entity ID in English, name in Russian.
        ("ru", "sensor.robot_room_queue", "Robot Очередь комнат"),
    ],
)
async def test_sensor_name_follows_language(
    hass: HomeAssistant,
    vacuum: Vacuum,
    entry: MockConfigEntry,
    language: str,
    entity_id: str,
    name: str,
) -> None:
    """A vacuum without a device: HA builds name and ID by its language rules."""
    hass.config.language = language
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.name == name


def test_listed_as_integration_not_helper() -> None:
    """Helper-type entries are hidden on the integrations page (found in 0.2.0)."""
    manifest = json.loads(
        (
            Path(__file__).parents[1] / "custom_components" / DOMAIN / "manifest.json"
        ).read_text("utf-8")
    )
    assert manifest["integration_type"] == "service"


async def test_remove_entry_cleans_up(
    hass: HomeAssistant, vacuum: Vacuum, entry: MockConfigEntry, hass_storage: dict
) -> None:
    assert await async_setup_component(hass, "lovelace", {})
    from homeassistant.components.lovelace.const import LOVELACE_DATA  # noqa: PLC0415

    resources = hass.data[LOVELACE_DATA].resources
    url = f"{vacuum_room_queue.CARD_URL}?v=1"
    await vacuum_room_queue._async_register_card_resource(hass, url)
    other = MockConfigEntry(domain=DOMAIN, data=dict(entry.data))
    other.add_to_hass(hass)
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {"version": 1, "key": key, "data": {"queue": []}}

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
    # Another queue is left: the card stays.
    assert any(i["url"] == url for i in resources.async_items())

    await hass.config_entries.async_remove(other.entry_id)
    await hass.async_block_till_done()
    assert not any(
        i["url"].startswith(vacuum_room_queue.CARD_URL) for i in resources.async_items()
    )


async def test_remove_card_resource_without_lovelace(hass: HomeAssistant) -> None:
    await vacuum_room_queue._async_remove_card_resource(hass)
