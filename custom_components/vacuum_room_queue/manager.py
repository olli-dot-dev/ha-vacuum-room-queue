"""Room queue of one vacuum.

The vacuum only ever gets one room at a time. The rest of the queue stays
here, so it can be reordered, shortened or extended at any time, also while
the vacuum is cleaning. When the vacuum reports that it is done with the
current room (it leaves the "cleaning" state for "returning", "docked" or
"idle"), the next room of the queue is sent to it.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.vacuum.const import (
    DOMAIN as VACUUM_DOMAIN,
)
from homeassistant.components.vacuum.const import (
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DOMAIN,
    ATTR_ENTITY_ID,
    ATTR_SERVICE,
    ATTR_SERVICE_DATA,
    ATTR_SUPPORTED_FEATURES,
    ENTITY_MATCH_ALL,
    EVENT_CALL_SERVICE,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AREAS,
    CONF_METHOD,
    CONF_SCRIPT,
    CONF_START_TIMEOUT,
    CONF_VACUUM,
    DEFAULT_START_TIMEOUT_MINUTES,
    DISPATCH_GRACE_SECONDS,
    DOMAIN,
    METHOD_CLEAN_AREA,
    STORAGE_VERSION,
    QueueError,
    QueueStatus,
)
from .mapping import mapped_areas

_LOGGER = logging.getLogger(__name__)

# Sent to the vacuum from elsewhere in Home Assistant (tile card, automation,
# voice) these end the run. Without this the robot's "returning"/"idle" would
# look like a finished room and the next room would be sent.
_ABORT_SERVICES = {"stop", "return_to_base"}

_FINISHED_ACTIVITIES = {
    VacuumActivity.RETURNING,
    VacuumActivity.DOCKED,
    VacuumActivity.IDLE,
}


@dataclass(frozen=True, slots=True)
class Room:
    """A room that can be put into the queue."""

    id: str
    name: str


class RoomQueueManager:
    """Keeps the room queue of one vacuum and drives the vacuum through it."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the manager from a config entry."""
        self.hass = hass
        self.entry = entry
        self.vacuum_entity_id: str = entry.data[CONF_VACUUM]
        options = {**entry.data, **entry.options}
        self.method: str = options.get(CONF_METHOD, METHOD_CLEAN_AREA)
        self.script: str | None = options.get(CONF_SCRIPT)
        self.configured_areas: list[str] = list(options.get(CONF_AREAS) or [])
        self.start_timeout: float = 60 * float(
            options.get(CONF_START_TIMEOUT, DEFAULT_START_TIMEOUT_MINUTES)
        )

        self.queue: list[str] = []
        self.current: str | None = None
        self.done: list[str] = []
        # Small history for the card: when the rooms of this run were
        # finished, and the rooms of the run before.
        self.done_at: list[str] = []
        self.run_started: str | None = None
        self.previous_run: dict[str, Any] | None = None
        # When each area was last finished by the integration (ISO, UTC).
        self.last_cleaned: dict[str, str] = {}
        self.status = QueueStatus.IDLE
        self.error: QueueError | None = None

        self._seen_cleaning = False
        self._in_grace = False
        # Contexts of our own service calls, to tell them from the user's.
        self._own_contexts: deque[str] = deque(maxlen=20)
        self._unsub_grace: CALLBACK_TYPE | None = None
        self._unsub_timeout: CALLBACK_TYPE | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listeners: list[CALLBACK_TYPE] = []
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )

    # ------------------------------------------------------------------
    # Lifecycle

    async def async_setup(self) -> None:
        """Restore the saved queue and start following the vacuum."""
        await self._async_restore()
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [self.vacuum_entity_id], self._async_on_vacuum_state
            )
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                EVENT_CALL_SERVICE,
                self._async_on_service_call,
                event_filter=_is_abort_call,
            )
        )
        # Room names and the area mapping of the vacuum are shown in the
        # card, so registry changes need to reach it as well.
        self._unsubs.append(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._async_on_registry_update
            )
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                ar.EVENT_AREA_REGISTRY_UPDATED, self._async_on_registry_update
            )
        )

    @callback
    def async_unload(self) -> None:
        """Stop following the vacuum."""
        self._cancel_timers()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> Callable[[], None]:
        """Call update_callback whenever the queue changes."""
        self._listeners.append(update_callback)

        @callback
        def remove() -> None:
            self._listeners.remove(update_callback)

        return remove

    # ------------------------------------------------------------------
    # Rooms

    @property
    def available_rooms(self) -> list[Room]:
        """Rooms that can be put into the queue, in display order."""
        area_reg = ar.async_get(self.hass)
        if self.method == METHOD_CLEAN_AREA:
            mapped = mapped_areas(self.hass, self.vacuum_entity_id)
            if self.configured_areas:
                ids = [area for area in self.configured_areas if area in mapped]
            else:
                ids = mapped
        else:
            ids = self.configured_areas
        areas = ((area_id, area_reg.async_get_area(area_id)) for area_id in ids)
        return [Room(area_id, area.name) for area_id, area in areas if area]

    @property
    def rooms_hint(self) -> str | None:
        """Why no room can be added, for the card (None if there are rooms)."""
        if self.available_rooms:
            return None
        if self.method == METHOD_CLEAN_AREA and not mapped_areas(
            self.hass, self.vacuum_entity_id
        ):
            return "no_mapping"
        return "no_rooms"

    @property
    def history(self) -> dict[str, Any]:
        """Return the current and the previous run, for the card."""
        return {
            "current": self._run(self.run_started, self.done, self.done_at),
            "previous": self.previous_run,
        }

    @staticmethod
    def _run(
        started: str | None, done: list[str], done_at: list[str]
    ) -> dict[str, Any]:
        # done_at may be shorter after an update from a version without it.
        times: list[str | None] = [*done_at, *[None] * (len(done) - len(done_at))]
        return {
            "started": started,
            "rooms": [
                {"area": area, "finished": at}
                for area, at in zip(done, times, strict=True)
            ],
        }

    def _archive_run(self) -> None:
        """Make the current run the previous one (if anything was cleaned)."""
        if self.done:
            self.previous_run = self._run(self.run_started, self.done, self.done_at)
        self.done = []
        self.done_at = []
        self.run_started = None

    def _last_cleaned_from_history(self) -> dict[str, str]:
        """Seed the last cleaning per area from the history (older stores)."""
        last: dict[str, str] = {}
        runs = [self.previous_run, self._run(self.run_started, self.done, self.done_at)]
        for run in runs:
            for room in (run or {}).get("rooms") or []:
                area, finished = room.get("area"), room.get("finished")
                if area and finished and finished > last.get(area, ""):
                    last[area] = finished
        return last

    def room_name(self, area_id: str) -> str:
        """Return the display name of an area."""
        area = ar.async_get(self.hass).async_get_area(area_id)
        return area.name if area is not None else area_id

    def _validate_areas(self, areas: list[str]) -> list[str]:
        known = {room.id for room in self.available_rooms}
        unknown = [area for area in areas if area not in known]
        if unknown:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_area",
                translation_placeholders={"areas": ", ".join(dict.fromkeys(unknown))},
            )
        # A room may appear several times: it is cleaned that many times.
        return areas

    # ------------------------------------------------------------------
    # Queue editing - allowed at any time

    async def async_set_queue(self, areas: list[str]) -> None:
        """Replace the waiting rooms."""
        self.queue = self._validate_areas(list(areas))
        self._changed()

    async def async_add_area(self, area_id: str, position: int | None = None) -> None:
        """Add a room to the queue, also when it is queued or cleaned already."""
        self._validate_areas([area_id])
        queue = list(self.queue)
        if position is None or position >= len(queue):
            queue.append(area_id)
        else:
            queue.insert(max(position, 0), area_id)
        self.queue = queue
        self._changed()

    async def async_remove_area(
        self, area_id: str, position: int | None = None
    ) -> None:
        """Remove a room.

        With a position only that entry of the queue goes, otherwise every
        waiting entry of the room, and the room is skipped if it is being
        cleaned right now.
        """
        if position is not None:
            if 0 <= position < len(self.queue) and self.queue[position] == area_id:
                self.queue = [*self.queue[:position], *self.queue[position + 1 :]]
                self._changed()
            return
        if area_id in self.queue:
            self.queue = [area for area in self.queue if area != area_id]
            self._changed()
        if area_id == self.current:
            await self.async_skip()

    async def async_clear(self) -> None:
        """Remove all waiting rooms; the current room is finished."""
        self.queue = []
        if self.current is None:
            self._archive_run()
        self._changed()

    # ------------------------------------------------------------------
    # Controlling the vacuum

    async def async_start(self, areas: list[str] | None = None) -> None:
        """Start the queue, resume it, or retry after an error."""
        if areas is not None:
            await self.async_set_queue(areas)
        if self.status == QueueStatus.RUNNING:
            return
        if self.status == QueueStatus.PAUSED and self.current is not None:
            await self._async_vacuum_service("start")
            self.status = QueueStatus.RUNNING
            self._changed()
            return
        if self.current is not None:
            # Error: send the same room again.
            await self._async_dispatch(self.current)
            return
        if not self.queue:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="queue_empty"
            )
        self._archive_run()
        self.run_started = dt_util.utcnow().isoformat()
        await self._async_dispatch(self.queue.pop(0))

    async def async_pause(self) -> None:
        """Pause the vacuum, the queue waits."""
        if self.status != QueueStatus.RUNNING:
            return
        await self._async_vacuum_service("pause")
        self.status = QueueStatus.PAUSED
        self._changed()

    async def async_skip(self) -> None:
        """Leave the current room and go on with the next one."""
        if self.current is None:
            return
        if self.queue:
            await self._async_dispatch(self.queue.pop(0))
            return
        self.current = None
        self.status = QueueStatus.IDLE
        self.error = None
        self._cancel_timers()
        self._changed()
        await self._async_send_home()

    async def async_stop(self) -> None:
        """Send the vacuum home; the current room goes back into the queue."""
        if self.current is not None:
            self.queue = [self.current, *self.queue]
            self.current = None
        was_active = self.status != QueueStatus.IDLE
        self.status = QueueStatus.IDLE
        self.error = None
        self._cancel_timers()
        self._changed()
        if was_active:
            await self._async_send_home()

    async def _async_dispatch(self, area_id: str) -> None:
        """Send one room to the vacuum."""
        self._cancel_timers()
        self.current = area_id
        self.status = QueueStatus.RUNNING
        self.error = None
        self._seen_cleaning = False
        self._in_grace = True
        self._unsub_grace = async_call_later(
            self.hass, DISPATCH_GRACE_SECONDS, self._async_grace_over
        )
        self._unsub_timeout = async_call_later(
            self.hass, self.start_timeout, self._async_start_timeout
        )
        self._changed()
        try:
            await self._async_clean(area_id)
        except HomeAssistantError:
            self._cancel_timers()
            self.status = QueueStatus.ERROR
            self.error = QueueError.COMMAND_FAILED
            self._changed()
            raise

    async def _async_clean(self, area_id: str) -> None:
        if self.method == METHOD_CLEAN_AREA:
            await self.hass.services.async_call(
                VACUUM_DOMAIN,
                "clean_area",
                {ATTR_ENTITY_ID: self.vacuum_entity_id, "cleaning_area_id": [area_id]},
                blocking=True,
                context=self._own_context(),
            )
            return
        await self.hass.services.async_call(
            "script",
            "turn_on",
            {
                ATTR_ENTITY_ID: self.script,
                "variables": {
                    "area_id": area_id,
                    "area_name": self.room_name(area_id),
                    "vacuum_entity_id": self.vacuum_entity_id,
                },
            },
            blocking=True,
            # A script that stops the vacuum before sending it to the room
            # runs in a child context of this one.
            context=self._own_context(),
        )

    async def _async_vacuum_service(self, service: str) -> None:
        await self.hass.services.async_call(
            VACUUM_DOMAIN,
            service,
            {ATTR_ENTITY_ID: self.vacuum_entity_id},
            blocking=True,
            context=self._own_context(),
        )

    def _own_context(self) -> Context:
        context = Context()
        self._own_contexts.append(context.id)
        return context

    async def _async_send_home(self) -> None:
        state = self.hass.states.get(self.vacuum_entity_id)
        if state is None or state.state in (
            VacuumActivity.DOCKED,
            VacuumActivity.RETURNING,
        ):
            return
        features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
        service = (
            "return_to_base" if features & VacuumEntityFeature.RETURN_HOME else "stop"
        )
        await self._async_vacuum_service(service)

    # ------------------------------------------------------------------
    # Following the vacuum

    @callback
    def _async_on_service_call(self, event: Event[Any]) -> None:
        """Stop/home sent to the vacuum by someone else ends the run."""
        if self.current is None or self.status == QueueStatus.IDLE:
            return
        context = event.context
        if context.id in self._own_contexts or (
            context.parent_id is not None and context.parent_id in self._own_contexts
        ):
            return
        service_data = event.data.get(ATTR_SERVICE_DATA) or {}
        if service_data.get(ATTR_ENTITY_ID) != ENTITY_MATCH_ALL:
            selected = async_extract_referenced_entity_ids(
                self.hass, TargetSelection(service_data)
            )
            if self.vacuum_entity_id not in (
                selected.referenced | selected.indirectly_referenced
            ):
                return
        _LOGGER.debug(
            "%s was told to %s, ending the queue run",
            self.vacuum_entity_id,
            event.data.get(ATTR_SERVICE),
        )
        # Like the card's stop, but the vacuum is already on its way.
        self.queue = [self.current, *self.queue]
        self.current = None
        self.status = QueueStatus.IDLE
        self.error = None
        self._cancel_timers()
        self._changed()

    @callback
    def _async_on_vacuum_state(self, event: Event[EventStateChangedData]) -> None:
        if (new_state := event.data["new_state"]) is not None:
            self._async_handle_activity(new_state.state)

    @callback
    def _async_handle_activity(self, activity: str) -> None:
        if self.current is None:
            return
        if activity == VacuumActivity.CLEANING:
            self._seen_cleaning = True
            self._cancel_timeout()
            if self.status in (QueueStatus.PAUSED, QueueStatus.ERROR):
                # Resumed in the vendor app or on the robot itself.
                self.status = QueueStatus.RUNNING
                self.error = None
            self._changed()
        elif activity == VacuumActivity.PAUSED:
            if self.status == QueueStatus.RUNNING and self._seen_cleaning:
                self.status = QueueStatus.PAUSED
                self._changed()
        elif activity == VacuumActivity.ERROR:
            if self.status in (QueueStatus.RUNNING, QueueStatus.PAUSED):
                self.status = QueueStatus.ERROR
                self.error = QueueError.VACUUM_ERROR
                self._changed()
        elif activity in _FINISHED_ACTIVITIES:
            self._async_handle_finished(activity)

    @callback
    def _async_handle_finished(self, activity: str) -> None:
        if (
            self.status == QueueStatus.RUNNING
            and self._seen_cleaning
            and not self._in_grace
        ):
            self.hass.async_create_task(
                self._async_room_finished(activity), eager_start=True
            )
        elif self.status == QueueStatus.PAUSED and activity == VacuumActivity.DOCKED:
            # Sent home while paused: the run is over, keep the room.
            if self.current is not None:
                self.queue = [self.current, *self.queue]
            self.current = None
            self.status = QueueStatus.IDLE
            self._cancel_timers()
            self._changed()

    async def _async_room_finished(self, activity: str) -> None:
        if self.current is not None:
            self.done = [*self.done, self.current]
            finished = dt_util.utcnow().isoformat()
            self.done_at = [*self.done_at, finished]
            self.last_cleaned = {**self.last_cleaned, self.current: finished}
        if self.queue:
            try:
                await self._async_dispatch(self.queue.pop(0))
            except HomeAssistantError:
                _LOGGER.exception(
                    "Could not send the next room to %s", self.vacuum_entity_id
                )
            return
        self.current = None
        self.status = QueueStatus.IDLE
        self._cancel_timers()
        self._changed()
        if activity == VacuumActivity.IDLE:
            try:
                await self._async_send_home()
            except HomeAssistantError:
                _LOGGER.warning("Could not send %s home", self.vacuum_entity_id)

    @callback
    def _async_grace_over(self, _now: datetime) -> None:
        self._unsub_grace = None
        self._in_grace = False
        # Judge the vacuum by its state now. This catches a robot that was
        # already cleaning (no state change) and one that finished during
        # the grace period.
        if (state := self.hass.states.get(self.vacuum_entity_id)) is not None:
            self._async_handle_activity(state.state)

    @callback
    def _async_start_timeout(self, _now: datetime) -> None:
        self._unsub_timeout = None
        if self.status == QueueStatus.RUNNING and not self._seen_cleaning:
            _LOGGER.warning(
                "%s did not start cleaning %s", self.vacuum_entity_id, self.current
            )
            self.status = QueueStatus.ERROR
            self.error = QueueError.DID_NOT_START
            self._changed()

    @callback
    def _async_on_registry_update(self, event: Event[Any]) -> None:
        if (
            event.event_type == er.EVENT_ENTITY_REGISTRY_UPDATED
            and event.data.get("entity_id") != self.vacuum_entity_id
        ):
            return
        self._notify()

    def _cancel_timeout(self) -> None:
        if self._unsub_timeout is not None:
            self._unsub_timeout()
            self._unsub_timeout = None

    def _cancel_timers(self) -> None:
        self._cancel_timeout()
        if self._unsub_grace is not None:
            self._unsub_grace()
            self._unsub_grace = None
        self._in_grace = False

    # ------------------------------------------------------------------
    # State and persistence

    @callback
    def _changed(self) -> None:
        self._store.async_delay_save(self._data_to_save, 1)
        self._notify()

    @callback
    def _notify(self) -> None:
        for update_callback in list(self._listeners):
            update_callback()

    def _data_to_save(self) -> dict[str, Any]:
        return {
            "queue": self.queue,
            "current": self.current,
            "done": self.done,
            "done_at": self.done_at,
            "run_started": self.run_started,
            "previous_run": self.previous_run,
            "last_cleaned": self.last_cleaned,
            "status": self.status,
            "error": self.error,
        }

    async def _async_restore(self) -> None:
        data = await self._store.async_load()
        if not data:
            return
        self.queue = list(data.get("queue") or [])
        self.current = data.get("current")
        self.done = list(data.get("done") or [])
        self.done_at = list(data.get("done_at") or [])[: len(self.done)]
        self.run_started = data.get("run_started")
        previous = data.get("previous_run")
        self.previous_run = previous if isinstance(previous, dict) else None
        last = data.get("last_cleaned")
        self.last_cleaned = (
            {str(k): str(v) for k, v in last.items() if v}
            if isinstance(last, dict)
            else self._last_cleaned_from_history()
        )
        try:
            self.status = QueueStatus(data.get("status", QueueStatus.IDLE))
        except ValueError:
            self.status = QueueStatus.IDLE
        error = data.get("error")
        self.error = QueueError(error) if error in set(QueueError) else None

        if self.current is None:
            self.status = QueueStatus.IDLE
            self.error = None
            return
        state = self.hass.states.get(self.vacuum_entity_id)
        activity = state.state if state is not None else None
        if self.status == QueueStatus.RUNNING:
            if activity in (None, "unavailable", "unknown", VacuumActivity.CLEANING):
                # Cleaning, or the vacuum integration is not loaded yet: go
                # on following it, its next state decides.
                self._seen_cleaning = True
            elif activity == VacuumActivity.PAUSED:
                self.status = QueueStatus.PAUSED
            else:
                # The vacuum stopped while Home Assistant was down; we
                # cannot know whether the room was finished.
                self.queue = [self.current, *self.queue]
                self.current = None
                self.status = QueueStatus.IDLE


@callback
def _is_abort_call(event_data: Mapping[str, Any]) -> bool:
    return (
        event_data.get(ATTR_DOMAIN) == VACUUM_DOMAIN
        and event_data.get(ATTR_SERVICE) in _ABORT_SERVICES
    )
