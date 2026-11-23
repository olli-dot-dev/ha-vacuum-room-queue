"""Vacuum Room Queue - clean rooms in an order that can change at any time."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

try:
    import probatio as vol
except ImportError:  # pragma: no cover - Home Assistant before 2026.10
    import voluptuous as vol  # type: ignore[no-redef]
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.components.websocket_api import async_register_command
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.decorators import (
    async_response,
    websocket_command,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_AREA_ID,
    ATTR_AREAS,
    ATTR_POSITION,
    DOMAIN,
    SERVICE_ADD_AREA,
    SERVICE_CLEAR,
    SERVICE_PAUSE,
    SERVICE_REMOVE_AREA,
    SERVICE_SET_QUEUE,
    SERVICE_SKIP,
    SERVICE_START,
    SERVICE_STOP,
    STORAGE_VERSION,
)
from .manager import RoomQueueManager

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS: list[Platform] = [Platform.SENSOR]

CARD_URL = f"/{DOMAIN}/vacuum-room-queue-card.js"
CARD_PATH = Path(__file__).parent / "frontend" / "vacuum-room-queue-card.js"
CARD_TRANSLATIONS = Path(__file__).parent / "card_translations"

type RoomQueueConfigEntry = ConfigEntry[RoomQueueManager]

_BASE_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})
_AREAS_SCHEMA = _BASE_SCHEMA.extend(
    {vol.Required(ATTR_AREAS): vol.All(cv.ensure_list, [cv.string])}
)
_START_SCHEMA = _BASE_SCHEMA.extend(
    {vol.Optional(ATTR_AREAS): vol.All(cv.ensure_list, [cv.string])}
)
_ADD_SCHEMA = _BASE_SCHEMA.extend(
    {
        vol.Required(ATTR_AREA_ID): cv.string,
        vol.Optional(ATTR_POSITION): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)
_REMOVE_SCHEMA = _BASE_SCHEMA.extend(
    {
        vol.Required(ATTR_AREA_ID): cv.string,
        vol.Optional(ATTR_POSITION): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services and serve the dashboard card."""
    _async_register_services(hass)
    async_register_command(hass, _ws_get_translations)

    if hass.http is None or "frontend" not in hass.config.components:
        return True
    # The content hash in the URL makes browsers fetch every changed card,
    # so the file itself may be cached for long.
    digest = await hass.async_add_executor_job(_card_digest)
    url = f"{CARD_URL}?v={digest}"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=True)]
    )
    # Both on purpose (lesson from ha-action-suggestion): a real Lovelace
    # resource is awaited before cards are created, add_extra_js_url also
    # covers YAML-mode dashboards. The card guards against a double load.
    await _async_register_card_resource(hass, url)
    add_extra_js_url(hass, url)
    return True


def _read_card_translations(language: str) -> tuple[str, dict[str, str]]:
    """Return (resolved language, strings) for the dashboard card.

    The card is a custom element, so HA's own translation pipeline
    (translations/, used for the config flow etc.) does not reach it. Its
    strings live in card_translations/<lang>.json instead and are served by
    the vacuum_room_queue/get_translations command. Unknown languages fall
    back to English; the resolved language goes back to the card as well.
    """
    lang = language.split("-", maxsplit=1)[0].lower()
    # isalpha() also keeps a crafted "language" from escaping the directory.
    if not lang.isalpha() or not (CARD_TRANSLATIONS / f"{lang}.json").is_file():
        lang = "en"
    path = CARD_TRANSLATIONS / f"{lang}.json"
    strings: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    return lang, strings


@websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/get_translations",
        vol.Required("language"): str,
    }
)
@async_response
async def _ws_get_translations(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Send the card its strings (every user, the card is not admin-only)."""
    lang, strings = await hass.async_add_executor_job(
        _read_card_translations, msg["language"]
    )
    connection.send_result(msg["id"], {"lang": lang, "strings": strings})


def _card_digest() -> str:
    return hashlib.sha256(CARD_PATH.read_bytes()).hexdigest()[:12]


async def _async_register_card_resource(hass: HomeAssistant, url: str) -> None:
    """Add or update the card as a Lovelace resource (storage mode only).

    Lovelace has no public API for this. Any failure only means that the
    add_extra_js_url fallback is the only way the card gets loaded.
    """
    try:
        from homeassistant.components.lovelace.const import (  # noqa: PLC0415
            LOVELACE_DATA,
            MODE_STORAGE,
        )

        lovelace_data = hass.data.get(LOVELACE_DATA)
        if lovelace_data is None or lovelace_data.resource_mode != MODE_STORAGE:
            return
        resources: Any = lovelace_data.resources
        await resources.async_load()
        existing = next(
            (
                item
                for item in resources.async_items()
                if item["url"].split("?", 1)[0] == CARD_URL
            ),
            None,
        )
        if existing is None:
            await resources.async_create_item({"res_type": "module", "url": url})
        elif existing["url"] != url:
            await resources.async_update_item(
                existing["id"], {"res_type": "module", "url": url}
            )
    except Exception:
        _LOGGER.debug("Could not register the card as Lovelace resource", exc_info=True)


async def _async_remove_card_resource(hass: HomeAssistant) -> None:
    """Remove the card's Lovelace resource again (storage mode only)."""
    try:
        from homeassistant.components.lovelace.const import (  # noqa: PLC0415
            LOVELACE_DATA,
            MODE_STORAGE,
        )

        lovelace_data = hass.data.get(LOVELACE_DATA)
        if lovelace_data is None or lovelace_data.resource_mode != MODE_STORAGE:
            return
        resources: Any = lovelace_data.resources
        await resources.async_load()
        for item in list(resources.async_items()):
            if item["url"].split("?", 1)[0] == CARD_URL:
                await resources.async_delete_item(item["id"])
    except Exception:
        _LOGGER.debug("Could not remove the card's Lovelace resource", exc_info=True)


async def async_setup_entry(hass: HomeAssistant, entry: RoomQueueConfigEntry) -> bool:
    """Set up the room queue of one vacuum."""
    manager = RoomQueueManager(hass, entry)
    await manager.async_setup()
    entry.runtime_data = manager
    entry.async_on_unload(manager.async_unload)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: RoomQueueConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up after a removed queue.

    Deletes the saved queue of this vacuum. When it was the last queue, the
    card's Lovelace resource goes as well, so nothing is left behind.
    """
    await Store[dict[str, Any]](
        hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
    ).async_remove()
    others = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if not others:
        await _async_remove_card_resource(hass)


def _get_manager(hass: HomeAssistant, entity_id: str) -> RoomQueueManager:
    """Find the queue by its sensor or by its vacuum."""
    registry = er.async_get(hass)
    entity = registry.async_get(entity_id)
    entry_id = entity.config_entry_id if entity is not None else None
    entry: RoomQueueConfigEntry
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        manager = entry.runtime_data
        if entry.entry_id == entry_id or manager.vacuum_entity_id == entity_id:
            return manager
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="unknown_queue",
        translation_placeholders={"entity_id": entity_id},
    )


def _async_register_services(hass: HomeAssistant) -> None:
    async def set_queue(call: ServiceCall) -> None:
        manager = _get_manager(hass, call.data[ATTR_ENTITY_ID])
        await manager.async_set_queue(call.data[ATTR_AREAS])

    async def add_area(call: ServiceCall) -> None:
        manager = _get_manager(hass, call.data[ATTR_ENTITY_ID])
        await manager.async_add_area(
            call.data[ATTR_AREA_ID], call.data.get(ATTR_POSITION)
        )

    async def remove_area(call: ServiceCall) -> None:
        manager = _get_manager(hass, call.data[ATTR_ENTITY_ID])
        await manager.async_remove_area(
            call.data[ATTR_AREA_ID], call.data.get(ATTR_POSITION)
        )

    async def start(call: ServiceCall) -> None:
        manager = _get_manager(hass, call.data[ATTR_ENTITY_ID])
        await manager.async_start(call.data.get(ATTR_AREAS))

    async def pause(call: ServiceCall) -> None:
        await _get_manager(hass, call.data[ATTR_ENTITY_ID]).async_pause()

    async def skip(call: ServiceCall) -> None:
        await _get_manager(hass, call.data[ATTR_ENTITY_ID]).async_skip()

    async def stop(call: ServiceCall) -> None:
        await _get_manager(hass, call.data[ATTR_ENTITY_ID]).async_stop()

    async def clear(call: ServiceCall) -> None:
        await _get_manager(hass, call.data[ATTR_ENTITY_ID]).async_clear()

    register = hass.services.async_register
    register(DOMAIN, SERVICE_SET_QUEUE, set_queue, schema=_AREAS_SCHEMA)
    register(DOMAIN, SERVICE_ADD_AREA, add_area, schema=_ADD_SCHEMA)
    register(DOMAIN, SERVICE_REMOVE_AREA, remove_area, schema=_REMOVE_SCHEMA)
    register(DOMAIN, SERVICE_START, start, schema=_START_SCHEMA)
    register(DOMAIN, SERVICE_PAUSE, pause, schema=_BASE_SCHEMA)
    register(DOMAIN, SERVICE_SKIP, skip, schema=_BASE_SCHEMA)
    register(DOMAIN, SERVICE_STOP, stop, schema=_BASE_SCHEMA)
    register(DOMAIN, SERVICE_CLEAR, clear, schema=_BASE_SCHEMA)
