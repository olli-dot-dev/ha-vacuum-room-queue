"""Config flow for Vacuum Room Queue: one entry per vacuum."""

from __future__ import annotations

from typing import Any

try:
    import probatio as vol
except ImportError:  # pragma: no cover - Home Assistant before 2026.10
    import voluptuous as vol  # type: ignore[no-redef]
from homeassistant.components.vacuum.const import DOMAIN as VACUUM_DOMAIN
from homeassistant.components.vacuum.const import VacuumEntityFeature
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    AreaSelector,
    AreaSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_AREAS,
    CONF_METHOD,
    CONF_SCRIPT,
    CONF_START_TIMEOUT,
    CONF_VACUUM,
    DEFAULT_START_TIMEOUT_MINUTES,
    DOMAIN,
    METHOD_CLEAN_AREA,
    METHOD_SCRIPT,
    START_TIMEOUT_RANGE_MINUTES,
)
from .mapping import async_unnamed_segments, mapped_areas


def _supports_clean_area(hass: HomeAssistant, entity_id: str) -> bool:
    state = hass.states.get(entity_id)
    features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) if state else 0
    return bool(features & VacuumEntityFeature.CLEAN_AREA)


def _method_schema(default: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_METHOD, default=default): SelectSelector(
                SelectSelectorConfig(
                    options=[METHOD_CLEAN_AREA, METHOD_SCRIPT],
                    translation_key=CONF_METHOD,
                    mode=SelectSelectorMode.LIST,
                )
            )
        }
    )


def _area_name(hass: HomeAssistant, area_id: str) -> str:
    area = ar.async_get(hass).async_get_area(area_id)
    return area.name if area else area_id


def _robot_rooms_selector(
    hass: HomeAssistant, vacuum: str, selected: list[str]
) -> SelectSelector:
    """Offer only the rooms the vacuum can go to (in mapping order).

    Rooms saved earlier but no longer mapped stay selectable, so the form
    still shows them and _validate_rooms can say which one is the problem.
    """
    room_ids = mapped_areas(hass, vacuum)
    room_ids += [a for a in selected if a not in room_ids]
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=a, label=_area_name(hass, a)) for a in room_ids
            ],
            multiple=True,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _rooms_placeholders(hass: HomeAssistant, vacuum: str) -> dict[str, str]:
    state = hass.states.get(vacuum)
    return {
        "vacuum": state.name if state else vacuum,
        "rooms": ", ".join(_area_name(hass, a) for a in mapped_areas(hass, vacuum)),
    }


def _rooms_schema(
    hass: HomeAssistant,
    vacuum: str,
    method: str,
    defaults: dict[str, Any],
    *,
    with_timeout: bool = False,
) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if method == METHOD_SCRIPT:
        script_key = (
            vol.Required(CONF_SCRIPT, default=defaults[CONF_SCRIPT])
            if defaults.get(CONF_SCRIPT)
            else vol.Required(CONF_SCRIPT)
        )
        fields[script_key] = EntitySelector(EntitySelectorConfig(domain="script"))
        fields[vol.Required(CONF_AREAS, default=defaults.get(CONF_AREAS, []))] = (
            AreaSelector(AreaSelectorConfig(multiple=True, reorder=True))
        )
    else:
        selected = list(defaults.get(CONF_AREAS, []))
        fields[vol.Optional(CONF_AREAS, default=selected)] = _robot_rooms_selector(
            hass, vacuum, selected
        )
    if with_timeout:
        low, high = START_TIMEOUT_RANGE_MINUTES
        fields[
            vol.Required(
                CONF_START_TIMEOUT,
                default=defaults.get(CONF_START_TIMEOUT, DEFAULT_START_TIMEOUT_MINUTES),
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=low,
                max=high,
                step=1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        )
    return vol.Schema(fields)


def _validate_rooms(
    hass: HomeAssistant, vacuum: str, method: str, user_input: dict[str, Any]
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (errors, description placeholders) for the rooms step."""
    if method == METHOD_SCRIPT and not user_input.get(CONF_AREAS):
        return {CONF_AREAS: "no_areas"}, {}
    if method == METHOD_CLEAN_AREA:
        mapped = set(mapped_areas(hass, vacuum))
        unmapped = [a for a in user_input.get(CONF_AREAS, []) if a not in mapped]
        if unmapped:
            names = [_area_name(hass, a) for a in unmapped]
            return {CONF_AREAS: "unmapped_areas"}, {"areas": ", ".join(names)}
    return {}, {}


class _MappingSteps:
    """Shared steps: stop and explain when the vacuum has no area mapping.

    Without a mapping native area cleaning has no rooms, so instead of
    creating an entry whose card stays empty the flow says how to fix it.
    """

    hass: HomeAssistant
    _method: str
    _segments: list[str] = []  # noqa: RUF012 - replaced per flow, never mutated

    @property
    def _vacuum_id(self) -> str:
        raise NotImplementedError

    async def async_step_rooms(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        raise NotImplementedError

    async def _async_after_method(self) -> ConfigFlowResult:
        """Go on to the rooms, or explain the missing mapping first."""
        if self._method == METHOD_CLEAN_AREA and not mapped_areas(
            self.hass, self._vacuum_id
        ):
            if segments := await async_unnamed_segments(self.hass, self._vacuum_id):
                self._segments = segments
                return await self.async_step_mapping_unnamed()
            return await self.async_step_mapping_missing()
        return await self.async_step_rooms()

    def _mapping_menu(
        self, step_id: str, extra: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        state = self.hass.states.get(self._vacuum_id)
        return self.async_show_menu(  # type: ignore[attr-defined,no-any-return]
            step_id=step_id,
            menu_options=["recheck", "use_script"],
            description_placeholders={
                "vacuum": state.name if state else self._vacuum_id,
                **(extra or {}),
            },
        )

    async def async_step_mapping_missing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain how to map the vacuum's segments to areas."""
        return self._mapping_menu("mapping_missing")

    async def async_step_mapping_unnamed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain that the segments need names in the vacuum's app first."""
        return self._mapping_menu(
            "mapping_unnamed", {"segments": ", ".join(self._segments)}
        )

    async def async_step_recheck(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check the mapping again (after the user mapped the areas)."""
        return await self._async_after_method()

    async def async_step_use_script(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use an own script per room instead."""
        self._method = METHOD_SCRIPT
        return await self.async_step_rooms()


class RoomQueueConfigFlow(_MappingSteps, ConfigFlow, domain=DOMAIN):
    """Pick the vacuum, then how it cleans a single room."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._vacuum: str = ""
        self._method: str = METHOD_CLEAN_AREA

    @property
    def _vacuum_id(self) -> str:
        return self._vacuum

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return RoomQueueOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the vacuum."""
        if user_input is not None:
            self._vacuum = user_input[CONF_VACUUM]
            entity = er.async_get(self.hass).async_get(self._vacuum)
            await self.async_set_unique_id(entity.id if entity else self._vacuum)
            self._abort_if_unique_id_configured()
            # Nothing to ask: native area cleaning if the vacuum can do it,
            # otherwise a script (the options still allow switching).
            self._method = (
                METHOD_CLEAN_AREA
                if _supports_clean_area(self.hass, self._vacuum)
                else METHOD_SCRIPT
            )
            return await self._async_after_method()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VACUUM): EntitySelector(
                        EntitySelectorConfig(domain=VACUUM_DOMAIN)
                    )
                }
            ),
        )

    async def async_step_rooms(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the rooms (and the script)."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            errors, placeholders = _validate_rooms(
                self.hass, self._vacuum, self._method, user_input
            )
            if not errors:
                state = self.hass.states.get(self._vacuum)
                return self.async_create_entry(
                    title=state.name if state else self._vacuum,
                    data={CONF_VACUUM: self._vacuum},
                    options={CONF_METHOD: self._method, **user_input},
                    # Open browser tabs only know the card after a reload.
                    description="reload_browser",
                )
        return self.async_show_form(
            step_id=f"rooms_{self._method}",
            data_schema=_rooms_schema(
                self.hass, self._vacuum, self._method, user_input or {}
            ),
            errors=errors,
            description_placeholders={
                **_rooms_placeholders(self.hass, self._vacuum),
                **placeholders,
            },
        )

    async_step_rooms_clean_area = async_step_rooms
    async_step_rooms_script = async_step_rooms


class RoomQueueOptionsFlow(_MappingSteps, OptionsFlow):
    """Change method, script and rooms."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._method: str = METHOD_CLEAN_AREA

    @property
    def _vacuum_id(self) -> str:
        return str(self.config_entry.data[CONF_VACUUM])

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the method."""
        if user_input is not None:
            self._method = user_input[CONF_METHOD]
            return await self._async_after_method()
        return self.async_show_form(
            step_id="init",
            data_schema=_method_schema(
                self.config_entry.options.get(CONF_METHOD, METHOD_CLEAN_AREA)
            ),
        )

    async def async_step_rooms(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the rooms (and the script)."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            errors, placeholders = _validate_rooms(
                self.hass, self._vacuum_id, self._method, user_input
            )
            if not errors:
                return self.async_create_entry(
                    data={CONF_METHOD: self._method, **user_input}
                )
        return self.async_show_form(
            step_id=f"rooms_{self._method}",
            data_schema=_rooms_schema(
                self.hass,
                self._vacuum_id,
                self._method,
                user_input or dict(self.config_entry.options),
                with_timeout=True,
            ),
            errors=errors,
            description_placeholders={
                **_rooms_placeholders(self.hass, self._vacuum_id),
                **placeholders,
            },
        )

    async_step_rooms_clean_area = async_step_rooms
    async_step_rooms_script = async_step_rooms
