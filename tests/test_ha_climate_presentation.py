"""Presentation resources retain control values and expose cached humidity only.

Uses real integration properties at HA stubs, not a deployed frontend renderer.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_ha_contract import AC_CAPABILITIES, capability_coordinator
from test_ha_contract import ha_modules as ha_modules

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components/ae200"


def load_resource(path):
    return json.loads((COMPONENT / path).read_text(encoding="utf-8"))


@pytest.mark.parametrize("language", ["en", "zh-Hans"])
def test_native_climate_option_labels_use_the_entity_translation_key(ha_modules, language):
    climate = importlib.import_module(ha_modules + ".climate")
    entity = climate.AE200Climate(capability_coordinator(capabilities=AC_CAPABILITIES), "1")
    translation_key = entity._attr_translation_key
    translated = load_resource(f"translations/{language}.json")["entity"]["climate"][translation_key]
    assert entity._attr_name is None and "name" not in translated
    assert entity._attr_unique_id == "fixture.invalid_1_climate"
    attributes = translated["state_attributes"]
    for attribute, mapping in (("fan_mode", climate._FAN_TO_HA), ("swing_mode", climate._SWING_TO_HA)):
        labels = attributes[attribute]["state"]
        assert set(labels) == set(mapping.values())
        assert all(isinstance(label, str) and label.strip() and "_" not in label for label in labels.values())
        assert len(labels) == len(set(labels.values()))
    assert entity.fan_modes == ["low", "medium_2", "medium_1", "high", "auto"]
    assert entity.swing_modes == ["fixed_horizontal", "mid_0", "mid_1", "mid_2", "fixed_vertical", "auto", "swing"]
    if language == "en":
        assert load_resource("strings.json")["entity"]["climate"][translation_key] == translated


def test_attribute_icons_cover_the_stable_values_at_the_native_lookup_path(ha_modules):
    climate = importlib.import_module(ha_modules + ".climate")
    icons = load_resource("icons.json")["entity"]["climate"][climate.AE200Climate._attr_translation_key]
    for attribute, mapping in (("fan_mode", climate._FAN_TO_HA), ("swing_mode", climate._SWING_TO_HA)):
        definition = icons["state_attributes"][attribute]
        assert set(definition["state"]) == set(mapping.values())
        assert definition["default"].startswith("mdi:")
        assert all(icon.startswith("mdi:") for icon in definition["state"].values())
    # HVAC menu icons remain Home Assistant's responsibility.
    assert "hvac_mode" not in icons["state_attributes"]


@pytest.mark.parametrize("stages,intermediate", [
    ("4STAGES", ["mid_1", "mid_2"]),
    ("5STAGES", ["mid_0", "mid_1", "mid_2"]),
])
def test_vane_labels_do_not_assign_a_global_position_number_across_stage_counts(ha_modules, stages, intermediate):
    climate = importlib.import_module(ha_modules + ".climate")
    coordinator = capability_coordinator(capabilities={**AC_CAPABILITIES, "AirStageSW": stages})
    entity = climate.AE200Climate(coordinator, "1")
    assert [mode for mode in entity.swing_modes if mode.startswith("mid_")] == intermediate
    for language in ("en", "zh-Hans"):
        labels = load_resource(f"translations/{language}.json")["entity"]["climate"][entity._attr_translation_key]
        selected = [labels["state_attributes"]["swing_mode"]["state"][mode] for mode in intermediate]
        assert all(not any(character.isdigit() for character in label) and "°" not in label for label in selected)


@pytest.mark.parametrize("attrs,expected", [
    ({"RoomHumidity": "45.5", "InletHumidity": "62"}, 45.5),
    ({"RoomHumidity": "", "InletHumidity": "62"}, 62),
    ({"RoomHumidity": "100"}, 100),
    ({"RoomHumidity": "0", "InletHumidity": "0"}, None),
    ({"RoomHumidity": "", "InletHumidity": ""}, None),
    ({"RoomHumidity": "*", "InletHumidity": "*"}, None),
    ({"RoomHumidity": "nan", "InletHumidity": "inf"}, None),
    ({"RoomHumidity": "101", "InletHumidity": "-1"}, None),
    ({}, None),
])
def test_climate_humidity_reads_the_existing_typed_cache_without_enabling_controls(ha_modules, attrs, expected):
    climate = importlib.import_module(ha_modules + ".climate")
    coordinator = capability_coordinator(capabilities=AC_CAPABILITIES)
    coordinator.client._request = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    entity = climate.AE200Climate(coordinator, "1")
    features = entity.supported_features
    coordinator.data["1"].update({"Drive": "OFF", "Mode": "COOL", **attrs})
    assert entity.current_humidity == expected
    assert entity.supported_features == features
    # TARGET_HUMIDITY is bit 4 in both supported HA API contracts.
    assert not int(entity.supported_features) & 4
    coordinator.client._request.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


def test_missing_group_has_no_climate_humidity(ha_modules):
    climate = importlib.import_module(ha_modules + ".climate")
    coordinator = capability_coordinator()
    coordinator.data.clear()
    assert climate.AE200Climate(coordinator, "1").current_humidity is None
