"""Every language has every string, with the same placeholders."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

COMPONENT = Path(__file__).parents[1] / "custom_components" / "vacuum_room_queue"
LANGUAGES = {"en", "de", "nl", "fr", "es", "pl", "ru"}
PLACEHOLDER = re.compile(r"\{\w+\}")


def _flat(node: Any, prefix: str = "") -> dict[str, str]:
    if isinstance(node, dict):
        out: dict[str, str] = {}
        for key, value in node.items():
            out.update(_flat(value, f"{prefix}{key}."))
        return out
    return {prefix.rstrip("."): node}


def _load(path: Path) -> dict[str, str]:
    return _flat(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("folder", "reference"),
    [("translations", "../strings.json"), ("card_translations", "en.json")],
)
def test_all_languages_complete(folder: str, reference: str) -> None:
    base = COMPONENT / folder
    assert {path.stem for path in base.glob("*.json")} == LANGUAGES
    ref = _load(base / reference)
    for path in base.glob("*.json"):
        strings = _load(path)
        assert strings.keys() == ref.keys(), path.name
        for key, text in strings.items():
            assert text.strip(), f"{path.name}: {key} is empty"
            assert set(PLACEHOLDER.findall(text)) == set(
                PLACEHOLDER.findall(ref[key])
            ), f"{path.name}: placeholders of {key}"
            assert text.count("**") == ref[key].count("**"), f"{path.name}: {key}"


def test_english_matches_strings() -> None:
    assert _load(COMPONENT / "translations" / "en.json") == _load(
        COMPONENT / "strings.json"
    )


@pytest.mark.parametrize(
    ("language", "resolved"),
    [
        ("de", "de"),
        ("nl-NL", "nl"),
        ("PL", "pl"),
        ("pt-BR", "en"),
        ("../strings", "en"),
        ("", "en"),
    ],
)
@pytest.mark.usefixtures("loaded")
async def test_card_translations_command(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    language: str,
    resolved: str,
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "vacuum_room_queue/get_translations", "language": language}
    )
    msg = await client.receive_json()
    assert msg["success"]
    assert msg["result"]["lang"] == resolved
    expected = json.loads(
        (COMPONENT / "card_translations" / f"{resolved}.json").read_text("utf-8")
    )
    assert msg["result"]["strings"] == expected


async def test_card_translations_for_non_admins(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
    loaded: MockConfigEntry,
) -> None:
    """The card is used by everyone in the household, not only admins."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(
        {"type": "vacuum_room_queue/get_translations", "language": "fr"}
    )
    msg = await client.receive_json()
    assert msg["success"]
    assert msg["result"]["lang"] == "fr"
