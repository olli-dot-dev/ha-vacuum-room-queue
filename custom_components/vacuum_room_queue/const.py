"""Constants for Vacuum Room Queue."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

DOMAIN: Final = "vacuum_room_queue"

CONF_VACUUM: Final = "vacuum"
CONF_METHOD: Final = "method"
CONF_SCRIPT: Final = "script"
CONF_AREAS: Final = "areas"
CONF_START_TIMEOUT: Final = "start_timeout"

METHOD_CLEAN_AREA: Final = "clean_area"
METHOD_SCRIPT: Final = "script"

ATTR_AREAS: Final = "areas"
ATTR_AREA_ID: Final = "area_id"
ATTR_POSITION: Final = "position"

SERVICE_SET_QUEUE: Final = "set_queue"
SERVICE_ADD_AREA: Final = "add_area"
SERVICE_REMOVE_AREA: Final = "remove_area"
SERVICE_START: Final = "start"
SERVICE_PAUSE: Final = "pause"
SERVICE_SKIP: Final = "skip"
SERVICE_STOP: Final = "stop"
SERVICE_CLEAR: Final = "clear"

STORAGE_VERSION: Final = 1

# After a room has been sent to the vacuum, its state is ignored for this
# long: many robots report a stale "idle"/"returning" for a few seconds
# before they switch to "cleaning", and a robot that was already cleaning
# another room may not change its state at all.
DISPATCH_GRACE_SECONDS: Final = 20
# A robot that has not reported "cleaning" this long after a room was sent
# to it most likely refused the command. Stations that wash or dry the mops
# first take a few minutes (a Dreame/Valetudo dock: ~3 min), so the default
# is generous and users can change it in the options.
DEFAULT_START_TIMEOUT_MINUTES: Final = 10
START_TIMEOUT_RANGE_MINUTES: Final = (1, 60)


class QueueStatus(StrEnum):
    """Status of a room queue."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


class QueueError(StrEnum):
    """Why a queue is in the error status."""

    VACUUM_ERROR = "vacuum_error"
    DID_NOT_START = "did_not_start"
    COMMAND_FAILED = "command_failed"
