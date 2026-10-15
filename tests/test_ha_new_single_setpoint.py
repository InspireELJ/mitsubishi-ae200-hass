"""NEW typed target corrections do not advertise or enable temperature writes."""

from __future__ import annotations

import importlib
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock

import pytest
from test_device_new_single_setpoint import CAPABILITIES, STATE
from test_ha_contract import capability_coordinator
from test_ha_contract import ha_modules as ha_modules


@pytest.mark.parametrize("mode,expected", [("COOL", 23), ("DRY", 23), ("HEAT", 21.5), ("AUTO", 24.5), ("FAN", None)])
def test_ha_uses_correct_cached_new_target_without_enabling_controls(ha_modules, mode, expected):
    classes = importlib.import_module(ha_modules + ".climate")
    coordinator = capability_coordinator(capabilities=CAPABILITIES, attrs={**STATE, "Mode": mode})
    coordinator.client._request = AsyncMock()
    entity = classes.AE200Climate(coordinator, "1")

    assert entity.target_temperature == expected
    assert entity.current_temperature == 25
    assert entity._attr_unique_id == "fixture.invalid_1_climate"
    assert not entity.supported_features & classes.ClimateEntityFeature.TARGET_TEMPERATURE
    assert entity.supported_features & classes.ClimateEntityFeature.TURN_ON
    assert entity.supported_features & classes.ClimateEntityFeature.TURN_OFF
    coordinator.client._request.assert_not_awaited()


@pytest.mark.parametrize("extra", [{}, {"hvac_mode": "cool"}, {"hvac_mode": "off"}])
@pytest.mark.asyncio
async def test_valid_new_profile_still_rejects_public_temperature_commands(ha_modules, extra):
    classes = importlib.import_module(ha_modules + ".climate")
    coordinator = capability_coordinator(capabilities=CAPABILITIES, attrs=STATE)
    coordinator.client._request = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    entity = classes.AE200Climate(coordinator, "1")

    with pytest.raises(ValueError, match="NEW group temperature fields"):
        await entity.async_set_temperature(temperature=23, **extra)

    coordinator.client._request.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_correction_does_not_add_setpoints_to_pure_mode_commands(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    coordinator = capability_coordinator(capabilities=CAPABILITIES, attrs=STATE)
    coordinator.client._request = AsyncMock(return_value="<Packet><Command>setResponse</Command></Packet>")
    coordinator.async_request_refresh = AsyncMock()

    await classes.AE200Climate(coordinator, "1").async_set_temperature(hvac_mode="cool")

    coordinator.client._request.assert_awaited_once()
    request = ET.fromstring(coordinator.client._request.call_args.args[0])
    assert request.find("./DatabaseManager/Mnet").attrib == {
        "Group": "1", "Drive": "ON", "Mode": "COOL",
    }
    coordinator.async_request_refresh.assert_awaited_once()


def test_unsupported_new_profile_is_unknown_in_ha_without_raw_loss(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    coordinator = capability_coordinator(
        capabilities={**CAPABILITIES, "AutoModeSWEx": "TWO_SETPOINT"}, attrs=STATE,
    )
    entity = classes.AE200Climate(coordinator, "1")
    assert entity.target_temperature is None
    assert coordinator.data["1"].raw_attributes() == STATE
    assert not entity.supported_features & classes.ClimateEntityFeature.TARGET_TEMPERATURE
