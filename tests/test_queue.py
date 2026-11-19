"""Tests for driving the vacuum through the room queue."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.vacuum_room_queue.const import (
    CONF_AREAS,
    CONF_METHOD,
    CONF_SCRIPT,
    CONF_START_TIMEOUT,
    CONF_VACUUM,
    DEFAULT_START_TIMEOUT_MINUTES,
    DISPATCH_GRACE_SECONDS,
    DOMAIN,
    METHOD_SCRIPT,
)

from .conftest import SENSOR, VACUUM, Vacuum

START_TIMEOUT_SECONDS = DEFAULT_START_TIMEOUT_MINUTES * 60


async def call(hass: HomeAssistant, service: str, **data: Any) -> None:
    await hass.services.async_call(
        DOMAIN, service, {"entity_id": SENSOR, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def wait(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def report(hass: HomeAssistant, vacuum: Vacuum, activity: str) -> None:
    vacuum.set(activity)
    await hass.async_block_till_done()


def attrs(hass: HomeAssistant) -> dict[str, Any]:
    state = hass.states.get(SENSOR)
    assert state is not None
    return dict(state.attributes)


def status(hass: HomeAssistant) -> str:
    state = hass.states.get(SENSOR)
    assert state is not None
    return state.state


@pytest.mark.usefixtures("loaded")
async def test_sensor_lists_mapped_rooms(
    hass: HomeAssistant, areas: dict[str, str]
) -> None:
    assert status(hass) == "idle"
    # Bath has no segments, so it is not offered.
    assert attrs(hass)["rooms"] == [
        {"id": areas["Kitchen"], "name": "Kitchen"},
        {"id": areas["Living"], "name": "Living"},
        {"id": areas["Hall"], "name": "Hall"},
    ]
    assert attrs(hass)["vacuum_entity_id"] == VACUUM
    assert attrs(hass)["rooms_hint"] is None


@pytest.mark.usefixtures("loaded")
async def test_full_run_with_changes_while_cleaning(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    kitchen, living, hall = areas["Kitchen"], areas["Living"], areas["Hall"]
    await call(hass, "set_queue", areas=[kitchen, living])
    await call(hass, "start")
    assert vacuum.cleaned() == [kitchen]
    assert status(hass) == "running"
    assert attrs(hass)["current_area"] == kitchen
    assert attrs(hass)["queue"] == [living]

    await report(hass, vacuum, "cleaning")
    await wait(hass, DISPATCH_GRACE_SECONDS + 1)

    # While cleaning the kitchen: reorder and add a room.
    await call(hass, "add_area", area_id=hall, position=0)
    assert attrs(hass)["queue"] == [hall, living]

    await report(hass, vacuum, "returning")
    assert vacuum.cleaned() == [kitchen, hall]
    assert attrs(hass)["current_area"] == hall
    assert attrs(hass)["done"] == [kitchen]

    # Stale "returning" states right after the dispatch are ignored.
    await report(hass, vacuum, "docked")
    assert attrs(hass)["current_area"] == hall

    await report(hass, vacuum, "cleaning")
    await wait(hass, DISPATCH_GRACE_SECONDS + 1)
    await call(hass, "remove_area", area_id=living)
    await report(hass, vacuum, "returning")

    assert vacuum.cleaned() == [kitchen, hall]
    assert status(hass) == "idle"
    assert attrs(hass)["current_area"] is None
    assert attrs(hass)["done"] == [kitchen, hall]
    # Returning already, nobody needs to send it home.
    assert vacuum.calls["return_to_base"] == []


@pytest.mark.usefixtures("loaded")
async def test_idle_after_last_room_sends_vacuum_home(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    await call(hass, "start", areas=[areas["Kitchen"]])
    await report(hass, vacuum, "cleaning")
    await wait(hass, DISPATCH_GRACE_SECONDS + 1)
    await report(hass, vacuum, "idle")
    assert status(hass) == "idle"
    assert len(vacuum.calls["return_to_base"]) == 1


@pytest.mark.usefixtures("loaded")
async def test_already_cleaning_robot_is_judged_after_grace(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    kitchen, living = areas["Kitchen"], areas["Living"]
    await call(hass, "start", areas=[kitchen, living])
    await report(hass, vacuum, "cleaning")
    await wait(hass, DISPATCH_GRACE_SECONDS + 1)

    # Skipping sends the next room; the state stays "cleaning".
    await call(hass, "skip")
    assert vacuum.cleaned() == [kitchen, living]
    await wait(hass, DISPATCH_GRACE_SECONDS + 1)
    await wait(hass, START_TIMEOUT_SECONDS + 1)
    assert status(hass) == "running"

    await report(hass, vacuum, "returning")
    assert status(hass) == "idle"
    # Skipped rooms are not done.
    assert attrs(hass)["done"] == [living]


@pytest.mark.usefixtures("loaded")
async def test_robot_that_does_not_start(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    await call(hass, "start", areas=[areas["Kitchen"]])
    await wait(hass, START_TIMEOUT_SECONDS + 1)
    assert status(hass) == "error"
    assert attrs(hass)["error"] == "did_not_start"

    # Retry sends the same room again.
    await call(hass, "start")
    assert vacuum.cleaned() == [areas["Kitchen"]] * 2
    await report(hass, vacuum, "cleaning")
    assert status(hass) == "running"
    assert attrs(hass)["error"] is None


@pytest.mark.usefixtures("loaded")
async def test_late_start_recovers_from_error(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    await call(hass, "start", areas=[areas["Kitchen"]])
    await wait(hass, START_TIMEOUT_SECONDS + 1)
    await report(hass, vacuum, "cleaning")
    assert status(hass) == "running"


@pytest.mark.usefixtures("loaded")
async def test_pause_resume_and_vacuum_error(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    await call(hass, "start", areas=[areas["Kitchen"], areas["Hall"]])
    await report(hass, vacuum, "cleaning")
    await call(hass, "pause")
    assert status(hass) == "paused"
    assert len(vacuum.calls["pause"]) == 1
    await report(hass, vacuum, "paused")

    await call(hass, "start")
    assert len(vacuum.calls["start"]) == 1
    assert status(hass) == "running"

    await report(hass, vacuum, "error")
    assert status(hass) == "error"
    assert attrs(hass)["error"] == "vacuum_error"
    # Fixed on the robot itself and resumed there.
    await report(hass, vacuum, "cleaning")
    assert status(hass) == "running"

    # Paused in the vendor app.
    await report(hass, vacuum, "paused")
    assert status(hass) == "paused"


@pytest.mark.usefixtures("loaded")
async def test_docked_while_paused_ends_the_run(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    await call(hass, "start", areas=[areas["Kitchen"], areas["Hall"]])
    await report(hass, vacuum, "cleaning")
    await report(hass, vacuum, "paused")
    await report(hass, vacuum, "returning")
    assert status(hass) == "paused"
    await report(hass, vacuum, "docked")
    assert status(hass) == "idle"
    assert attrs(hass)["queue"] == [areas["Kitchen"], areas["Hall"]]


@pytest.mark.usefixtures("loaded")
async def test_stop_puts_room_back(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    await call(hass, "start", areas=[areas["Kitchen"], areas["Hall"]])
    await report(hass, vacuum, "cleaning")
    await call(hass, "stop")
    assert status(hass) == "idle"
    assert attrs(hass)["queue"] == [areas["Kitchen"], areas["Hall"]]
    assert len(vacuum.calls["return_to_base"]) == 1
    # The robot driving home does not finish anything.
    await wait(hass, DISPATCH_GRACE_SECONDS + 1)
    await report(hass, vacuum, "returning")
    assert vacuum.cleaned() == [areas["Kitchen"]]


@pytest.mark.usefixtures("loaded")
async def test_skip_and_remove_last_room(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    await call(hass, "start", areas=[areas["Kitchen"]])
    await report(hass, vacuum, "cleaning")
    await call(hass, "remove_area", area_id=areas["Kitchen"])
    assert status(hass) == "idle"
    assert attrs(hass)["current_area"] is None
    assert len(vacuum.calls["return_to_base"]) == 1


@pytest.mark.usefixtures("loaded")
async def test_stop_without_return_home_uses_stop(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    await call(hass, "start", areas=[areas["Kitchen"]])
    vacuum.set("cleaning", features=0)
    await hass.async_block_till_done()
    await call(hass, "stop")
    assert len(vacuum.calls["stop"]) == 1


@pytest.mark.usefixtures("loaded")
async def test_queue_editing_rules(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    kitchen, living, hall = areas["Kitchen"], areas["Living"], areas["Hall"]
    # Rooms may repeat.
    await call(hass, "set_queue", areas=[kitchen, living, kitchen])
    assert attrs(hass)["queue"] == [kitchen, living, kitchen]
    await call(hass, "add_area", area_id=hall, position=1)
    assert attrs(hass)["queue"] == [kitchen, hall, living, kitchen]
    await call(hass, "add_area", area_id=hall, position=99)
    assert attrs(hass)["queue"] == [kitchen, hall, living, kitchen, hall]

    # Removing by position takes only that entry...
    await call(hass, "remove_area", area_id=kitchen, position=3)
    assert attrs(hass)["queue"] == [kitchen, hall, living, hall]
    # ...and nothing when the entry is not that room any more (stale card).
    await call(hass, "remove_area", area_id=kitchen, position=1)
    await call(hass, "remove_area", area_id=kitchen, position=42)
    assert attrs(hass)["queue"] == [kitchen, hall, living, hall]
    # Without a position every entry of the room goes.
    await call(hass, "remove_area", area_id=hall)
    assert attrs(hass)["queue"] == [kitchen, living]

    await call(hass, "start")
    # The current room can be queued again.
    await call(hass, "add_area", area_id=kitchen, position=0)
    assert attrs(hass)["queue"] == [kitchen, living]

    await call(hass, "clear")
    assert attrs(hass)["queue"] == []
    assert attrs(hass)["current_area"] == kitchen
    # Starting while running does nothing.
    await call(hass, "start")
    assert vacuum.cleaned() == [kitchen]


@pytest.mark.usefixtures("loaded")
async def test_room_cleaned_several_times(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    kitchen, hall = areas["Kitchen"], areas["Hall"]
    await call(hass, "start", areas=[kitchen, kitchen, hall])
    for _ in range(3):
        await report(hass, vacuum, "cleaning")
        await wait(hass, DISPATCH_GRACE_SECONDS + 1)
        await report(hass, vacuum, "returning")
    assert vacuum.cleaned() == [kitchen, kitchen, hall]
    assert attrs(hass)["done"] == [kitchen, kitchen, hall]
    assert status(hass) == "idle"


@pytest.mark.usefixtures("loaded")
async def test_remove_room_everywhere_also_skips_it(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    kitchen, hall = areas["Kitchen"], areas["Hall"]
    await call(hass, "start", areas=[kitchen, kitchen, hall, kitchen])
    await report(hass, vacuum, "cleaning")
    await call(hass, "remove_area", area_id=kitchen)
    assert attrs(hass)["current_area"] == hall
    assert attrs(hass)["queue"] == []
    assert vacuum.cleaned() == [kitchen, hall]


@pytest.mark.usefixtures("loaded")
async def test_invalid_requests(hass: HomeAssistant, areas: dict[str, str]) -> None:
    with pytest.raises(ServiceValidationError, match="bath"):
        await call(hass, "set_queue", areas=[areas["Bath"]])
    with pytest.raises(ServiceValidationError):
        await call(hass, "start")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "start", {"entity_id": "sensor.nope"}, blocking=True
        )
    # Nothing to pause, skip or stop.
    await call(hass, "pause")
    await call(hass, "skip")
    await call(hass, "stop")
    await call(hass, "remove_area", area_id=areas["Hall"])
    assert status(hass) == "idle"


@pytest.mark.usefixtures("loaded")
async def test_vacuum_entity_id_also_addresses_queue(
    hass: HomeAssistant, areas: dict[str, str]
) -> None:
    await hass.services.async_call(
        DOMAIN,
        "set_queue",
        {"entity_id": VACUUM, "areas": [areas["Hall"]]},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert attrs(hass)["queue"] == [areas["Hall"]]


@pytest.mark.usefixtures("loaded")
async def test_failing_command_is_an_error(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    async def fail(_call: Any) -> None:
        raise ServiceValidationError("nope")

    hass.services.async_register("vacuum", "clean_area", fail)
    with pytest.raises(ServiceValidationError):
        await call(hass, "start", areas=[areas["Kitchen"]])
    assert status(hass) == "error"
    assert attrs(hass)["error"] == "command_failed"


@pytest.mark.usefixtures("loaded")
async def test_failing_next_room_does_not_raise(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    await call(hass, "start", areas=[areas["Kitchen"], areas["Hall"]])
    await report(hass, vacuum, "cleaning")
    await wait(hass, DISPATCH_GRACE_SECONDS + 1)

    async def fail(_call: Any) -> None:
        raise ServiceValidationError("nope")

    hass.services.async_register("vacuum", "clean_area", fail)
    await report(hass, vacuum, "returning")
    assert status(hass) == "error"
    assert attrs(hass)["current_area"] == areas["Hall"]


@pytest.mark.usefixtures("loaded")
async def test_area_registry_changes_reach_the_sensor(
    hass: HomeAssistant, areas: dict[str, str]
) -> None:
    from homeassistant.helpers import area_registry as ar  # noqa: PLC0415

    ar.async_get(hass).async_update(areas["Hall"], name="Corridor")
    await hass.async_block_till_done()
    assert {"id": areas["Hall"], "name": "Corridor"} in attrs(hass)["rooms"]


async def test_script_method(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    script_calls = async_mock_service(hass, "script", "turn_on")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Robot",
        data={CONF_VACUUM: VACUUM},
        options={
            CONF_METHOD: METHOD_SCRIPT,
            CONF_SCRIPT: "script.clean_room",
            CONF_AREAS: [areas["Bath"], areas["Kitchen"]],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert [room["name"] for room in attrs(hass)["rooms"]] == ["Bath", "Kitchen"]

    await call(hass, "start", areas=[areas["Bath"]])
    assert script_calls[0].data == {
        "entity_id": "script.clean_room",
        "variables": {
            "area_id": areas["Bath"],
            "area_name": "Bath",
            "vacuum_entity_id": VACUUM,
        },
    }
    assert vacuum.calls["clean_area"] == []


async def test_options_limit_rooms(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_VACUUM: VACUUM},
        options={CONF_AREAS: [areas["Hall"], areas["Bath"], areas["Kitchen"]]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Bath is configured but has no segments.
    assert [room["name"] for room in attrs(hass)["rooms"]] == ["Hall", "Kitchen"]


@pytest.mark.parametrize(
    ("saved_status", "activity", "expected_status", "expected_current"),
    [
        ("running", "cleaning", "running", "Kitchen"),
        ("running", "paused", "paused", "Kitchen"),
        ("running", "docked", "idle", None),
        ("paused", "paused", "paused", "Kitchen"),
    ],
)
async def test_restore(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    vacuum: Vacuum,
    entry: MockConfigEntry,
    areas: dict[str, str],
    saved_status: str,
    activity: str,
    expected_status: str,
    expected_current: str | None,
) -> None:
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {
            "queue": [areas["Hall"]],
            "current": areas["Kitchen"],
            "done": [],
            "status": saved_status,
            "error": None,
        },
    }
    vacuum.set(activity)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert status(hass) == expected_status
    current = areas[expected_current] if expected_current else None
    assert attrs(hass)["current_area"] == current
    if current is None:
        assert attrs(hass)["queue"] == [areas["Kitchen"], areas["Hall"]]


async def test_restore_running_finishes_after_start(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    vacuum: Vacuum,
    entry: MockConfigEntry,
    areas: dict[str, str],
) -> None:
    """The vacuum is not loaded yet when Home Assistant starts."""
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {
            "queue": [areas["Hall"]],
            "current": areas["Kitchen"],
            "done": [],
            "status": "running",
            "error": "bogus",
        },
    }
    hass.states.async_remove(VACUUM)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert status(hass) == "running"
    await report(hass, vacuum, "returning")
    assert vacuum.cleaned() == [areas["Hall"]]


async def test_restore_idle_and_garbage(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    vacuum: Vacuum,
    entry: MockConfigEntry,
    areas: dict[str, str],
) -> None:
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {"queue": [areas["Hall"]], "current": None, "status": "weird"},
    }
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert status(hass) == "idle"
    assert attrs(hass)["queue"] == [areas["Hall"]]


async def test_unload(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(loaded.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(SENSOR)
    assert state is not None
    assert state.state == "unavailable"


@pytest.mark.usefixtures("loaded")
async def test_rooms_hint_when_mapping_disappears(hass: HomeAssistant) -> None:
    """The card says why there are no rooms (e.g. map reset in the vacuum)."""
    er.async_get(hass).async_update_entity_options(
        VACUUM, "vacuum", {"area_mapping": {}, "last_seen_segments": []}
    )
    await hass.async_block_till_done()
    assert attrs(hass)["rooms"] == []
    assert attrs(hass)["rooms_hint"] == "no_mapping"


async def test_rooms_hint_when_options_exclude_all(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_VACUUM: VACUUM},
        options={CONF_AREAS: [areas["Bath"]]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert attrs(hass)["rooms_hint"] == "no_rooms"


async def test_start_timeout_from_options(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    """Slow docks (mop washing): the wait for "cleaning" is configurable."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_VACUUM: VACUUM},
        options={CONF_AREAS: [], CONF_START_TIMEOUT: 2},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await call(hass, "start", areas=[areas["Kitchen"]])
    # wait() counts from now, not cumulatively.
    await wait(hass, 119)
    assert status(hass) == "running"
    await wait(hass, 121)
    assert status(hass) == "error"
    assert attrs(hass)["error"] == "did_not_start"


async def _run_rooms(hass: HomeAssistant, vacuum: Vacuum, rooms: list[str]) -> None:
    """Clean the rooms one after the other until the queue is idle."""
    await call(hass, "start", areas=rooms)
    for _ in rooms:
        await report(hass, vacuum, "cleaning")
        await wait(hass, DISPATCH_GRACE_SECONDS + 1)
        await report(hass, vacuum, "returning")
    assert status(hass) == "idle"


@pytest.mark.usefixtures("loaded")
async def test_history_of_this_and_previous_run(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    kitchen, hall = areas["Kitchen"], areas["Hall"]
    assert attrs(hass)["history"] == {
        "current": {"started": None, "rooms": []},
        "previous": None,
    }

    # A room cleaned twice shows up twice, each with its own time.
    await _run_rooms(hass, vacuum, [kitchen, kitchen])
    first = attrs(hass)["history"]
    assert first["current"]["started"] is not None
    assert [room["area"] for room in first["current"]["rooms"]] == [kitchen, kitchen]
    assert all(room["finished"] for room in first["current"]["rooms"])
    assert first["previous"] is None

    # A new run moves this one to "previous".
    await _run_rooms(hass, vacuum, [hall])
    second = attrs(hass)["history"]
    assert second["previous"] == first["current"]
    assert [room["area"] for room in second["current"]["rooms"]] == [hall]

    # Clearing while idle starts over, too; an empty run is not archived.
    await call(hass, "clear")
    third = attrs(hass)["history"]
    assert third["previous"] == second["current"]
    assert third["current"] == {"started": None, "rooms": []}
    await call(hass, "clear")
    assert attrs(hass)["history"]["previous"] == second["current"]

    # The last cleaning per room stays, independent of the runs.
    assert attrs(hass)["last_cleaned"] == {
        kitchen: first["current"]["rooms"][1]["finished"],
        hall: second["current"]["rooms"][0]["finished"],
    }


@pytest.mark.usefixtures("loaded")
async def test_skipped_room_is_not_last_cleaned(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    await call(hass, "start", areas=[areas["Kitchen"], areas["Hall"]])
    await report(hass, vacuum, "cleaning")
    await wait(hass, DISPATCH_GRACE_SECONDS + 1)
    await call(hass, "skip")
    assert attrs(hass)["last_cleaned"] == {}


async def test_history_survives_restart_and_old_storage(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    vacuum: Vacuum,
    entry: MockConfigEntry,
    areas: dict[str, str],
) -> None:
    previous = {
        "started": "2026-11-18T08:00:00+00:00",
        "rooms": [{"area": areas["Hall"], "finished": "2026-11-18T08:10:00+00:00"}],
    }
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {
            "queue": [],
            "current": None,
            # Saved by 0.1.0: no times yet.
            "done": [areas["Kitchen"]],
            "status": "idle",
            "error": None,
            "previous_run": previous,
        },
    }
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert attrs(hass)["history"] == {
        "current": {
            "started": None,
            "rooms": [{"area": areas["Kitchen"], "finished": None}],
        },
        "previous": previous,
    }
    # Older stores: the last cleaning per room comes from the history.
    assert attrs(hass)["last_cleaned"] == {areas["Hall"]: "2026-11-18T08:10:00+00:00"}


async def _vacuum_call(hass: HomeAssistant, service: str, **data: Any) -> None:
    """A service call from elsewhere in HA (tile card, automation)."""
    await hass.services.async_call("vacuum", service, data, blocking=True)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("service", "target", "then"),
    [
        ("return_to_base", {"entity_id": VACUUM}, "returning"),
        ("stop", {"entity_id": [VACUUM, "vacuum.other"]}, "idle"),
        ("stop", {"entity_id": "all"}, "idle"),
    ],
)
@pytest.mark.usefixtures("loaded")
async def test_stop_from_elsewhere_in_ha_ends_run(
    hass: HomeAssistant,
    vacuum: Vacuum,
    areas: dict[str, str],
    service: str,
    target: dict[str, Any],
    then: str,
) -> None:
    """Not mistaken for a finished room: no next room is sent."""
    kitchen, hall = areas["Kitchen"], areas["Hall"]
    await call(hass, "start", areas=[kitchen, hall])
    await report(hass, vacuum, "cleaning")
    await wait(hass, DISPATCH_GRACE_SECONDS + 1)

    await _vacuum_call(hass, service, **target)
    await report(hass, vacuum, then)
    assert status(hass) == "idle"
    assert attrs(hass)["current_area"] is None
    assert attrs(hass)["queue"] == [kitchen, hall]
    assert vacuum.cleaned() == [kitchen]


@pytest.mark.usefixtures("loaded")
async def test_stop_of_another_vacuum_is_ignored(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    await call(hass, "start", areas=[areas["Kitchen"]])
    await report(hass, vacuum, "cleaning")
    await _vacuum_call(hass, "stop", entity_id="vacuum.other")
    assert status(hass) == "running"


async def test_own_script_stopping_the_vacuum_is_not_an_abort(
    hass: HomeAssistant, vacuum: Vacuum, areas: dict[str, str]
) -> None:
    """A room script may stop the vacuum before sending it to the room."""

    async def clean_room(service_call: ServiceCall) -> None:
        await hass.services.async_call(
            "vacuum",
            "stop",
            {"entity_id": VACUUM},
            blocking=True,
            context=Context(parent_id=service_call.context.id),
        )

    hass.services.async_register("script", "turn_on", clean_room)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_VACUUM: VACUUM},
        options={
            CONF_METHOD: METHOD_SCRIPT,
            CONF_SCRIPT: "script.clean_room",
            CONF_AREAS: [areas["Bath"], areas["Kitchen"]],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await call(hass, "start", areas=[areas["Bath"], areas["Kitchen"]])
    assert len(vacuum.calls["stop"]) == 1
    assert status(hass) == "running"
    assert attrs(hass)["current_area"] == areas["Bath"]
