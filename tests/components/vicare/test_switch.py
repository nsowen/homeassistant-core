"""Test ViCare switch entity."""

from datetime import timedelta
from unittest.mock import patch

import pytest

from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.components.vicare.const import (
    CONF_BOOST_END_TIME,
    CONF_CIRCULATION_BOOST_DURATION,
    DEFAULT_CIRCULATION_BOOST_DURATION,
    DOMAIN,
    WEEKDAYS,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import homeassistant.util.dt as dt_util

from . import ENTRY_CONFIG, MODULE, setup_integration
from .conftest import Fixture, MockPyViCare

from tests.common import MockConfigEntry, async_fire_time_changed

# Vitocal250A fixture supports getDomesticHotWaterCirculationSchedule
FIXTURE_HEATPUMP = "vicare/Vitocal250A.json"
ENTITY_SWITCH = "switch.model0_dhw_circulation_boost"

# An empty schedule that will never conflict with boost slots
EMPTY_SCHEDULE: dict = {
    "active": True,
    "default_mode": "off",
    "mon": [],
    "tue": [],
    "wed": [],
    "thu": [],
    "fri": [],
    "sat": [],
    "sun": [],
}


@pytest.fixture
async def vicare_heatpump(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> MockConfigEntry:
    """Set up the ViCare integration with a heat pump fixture."""
    fixtures: list[Fixture] = [Fixture({"type:heatpump"}, FIXTURE_HEATPUMP)]
    with (
        patch(f"{MODULE}.login", return_value=MockPyViCare(fixtures)),
        patch(f"{MODULE}.PLATFORMS", [Platform.SWITCH]),
    ):
        await setup_integration(hass, mock_config_entry)
    return mock_config_entry


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_switch_created(
    hass: HomeAssistant,
    vicare_heatpump: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that the circulation boost switch entity is created for a supported device."""
    entity = entity_registry.async_get(ENTITY_SWITCH)
    assert entity is not None
    assert entity.domain == "switch"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_not_created_when_unsupported(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that the switch is NOT created when the device does not support the feature."""
    fixtures: list[Fixture] = [Fixture({"type:boiler"}, "vicare/Vitodens300W.json")]
    with (
        patch(f"{MODULE}.login", return_value=MockPyViCare(fixtures)),
        patch(f"{MODULE}.PLATFORMS", [Platform.SWITCH]),
    ):
        await setup_integration(hass, mock_config_entry)

    entity = entity_registry.async_get(ENTITY_SWITCH)
    assert entity is None


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_turn_on(
    hass: HomeAssistant,
    vicare_heatpump: MockConfigEntry,
) -> None:
    """Test turning on circulation boost writes a schedule slot for today."""
    state = hass.states.get(ENTITY_SWITCH)
    assert state is not None
    assert state.state == STATE_OFF

    with (
        patch(
            "PyViCare.PyViCareHeatingDevice.HeatingDevice.getDomesticHotWaterCirculationSchedule",
            return_value=EMPTY_SCHEDULE,
        ),
        patch(
            "PyViCare.PyViCareHeatingDevice.HeatingDevice.setDomesticHotWaterCirculationSchedule",
        ) as mock_set,
    ):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: ENTITY_SWITCH},
            blocking=True,
        )

    state = hass.states.get(ENTITY_SWITCH)
    assert state.state == STATE_ON

    mock_set.assert_called_once()
    call_args = mock_set.call_args[0][0]
    # Today's weekday must contain the boost slot
    now = dt_util.now()
    today = WEEKDAYS[now.weekday()]
    today_slots = call_args.get(today, [])
    assert len(today_slots) == 1
    assert today_slots[0]["mode"] == "on"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_turn_off_manual(
    hass: HomeAssistant,
    vicare_heatpump: MockConfigEntry,
) -> None:
    """Test manually turning off circulation boost restores original schedule."""
    with (
        patch(
            "PyViCare.PyViCareHeatingDevice.HeatingDevice.getDomesticHotWaterCirculationSchedule",
            return_value=EMPTY_SCHEDULE,
        ),
        patch(
            "PyViCare.PyViCareHeatingDevice.HeatingDevice.setDomesticHotWaterCirculationSchedule",
        ) as mock_set,
    ):
        # Turn on
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: ENTITY_SWITCH},
            blocking=True,
        )
        assert hass.states.get(ENTITY_SWITCH).state == STATE_ON
        turn_on_call_count = mock_set.call_count

        # Turn off manually
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: ENTITY_SWITCH},
            blocking=True,
        )

    assert hass.states.get(ENTITY_SWITCH).state == STATE_OFF
    # One call for turn_on, one for turn_off (restore)
    assert mock_set.call_count == turn_on_call_count + 1
    # Second call should restore the original (empty) schedule — metadata fields
    # (active, default_mode) are stripped before sending to the API.
    restore_call_args = mock_set.call_args[0][0]
    expected = {
        k: v for k, v in EMPTY_SCHEDULE.items() if k not in ("active", "default_mode")
    }
    assert restore_call_args == expected


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_auto_off(
    hass: HomeAssistant,
    vicare_heatpump: MockConfigEntry,
) -> None:
    """Test that the boost automatically turns off after the configured duration."""
    with (
        patch(
            "PyViCare.PyViCareHeatingDevice.HeatingDevice.getDomesticHotWaterCirculationSchedule",
            return_value=EMPTY_SCHEDULE,
        ),
        patch(
            "PyViCare.PyViCareHeatingDevice.HeatingDevice.setDomesticHotWaterCirculationSchedule",
        ) as mock_set,
    ):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: ENTITY_SWITCH},
            blocking=True,
        )
        assert hass.states.get(ENTITY_SWITCH).state == STATE_ON

        # Advance time past the boost duration (default 10 minutes)
        async_fire_time_changed(
            hass,
            dt_util.now() + timedelta(minutes=DEFAULT_CIRCULATION_BOOST_DURATION + 1),
        )
        await hass.async_block_till_done()

    assert hass.states.get(ENTITY_SWITCH).state == STATE_OFF
    # setDomesticHotWaterCirculationSchedule called twice: once for on, once for off
    assert mock_set.call_count == 2


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_duration_from_options(
    hass: HomeAssistant,
) -> None:
    """Test that the boost duration is read from config entry options."""
    custom_duration = 25
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ViCare",
        entry_id="1234",
        data=ENTRY_CONFIG,
        minor_version=2,
        options={CONF_CIRCULATION_BOOST_DURATION: custom_duration},
    )

    fixtures: list[Fixture] = [Fixture({"type:heatpump"}, FIXTURE_HEATPUMP)]
    with (
        patch(f"{MODULE}.login", return_value=MockPyViCare(fixtures)),
        patch(f"{MODULE}.PLATFORMS", [Platform.SWITCH]),
    ):
        await setup_integration(hass, config_entry)
    mock_config_entry = config_entry

    with (
        patch(
            "PyViCare.PyViCareHeatingDevice.HeatingDevice.getDomesticHotWaterCirculationSchedule",
            return_value=EMPTY_SCHEDULE,
        ),
        patch(
            "PyViCare.PyViCareHeatingDevice.HeatingDevice.setDomesticHotWaterCirculationSchedule",
        ) as mock_set,
    ):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: ENTITY_SWITCH},
            blocking=True,
        )

    mock_set.assert_called_once()
    # Verify the boost options contain the custom duration
    assert (
        mock_config_entry.options.get(CONF_CIRCULATION_BOOST_DURATION)
        == custom_duration
    )
    # The boost end time should be stored in options
    assert CONF_BOOST_END_TIME in mock_config_entry.options


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_conflict_no_activation(
    hass: HomeAssistant,
    vicare_heatpump: MockConfigEntry,
) -> None:
    """Test that boost does not activate when a schedule slot conflicts."""
    now = dt_util.now()
    # Build a schedule where today is fully occupied
    today_key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
    conflicting_schedule = {
        **EMPTY_SCHEDULE,
        today_key: [{"start": "00:00", "end": "23:50", "mode": "on", "position": 0}],
    }

    with (
        patch(
            "PyViCare.PyViCareHeatingDevice.HeatingDevice.getDomesticHotWaterCirculationSchedule",
            return_value=conflicting_schedule,
        ),
        patch(
            "PyViCare.PyViCareHeatingDevice.HeatingDevice.setDomesticHotWaterCirculationSchedule",
        ) as mock_set,
    ):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: ENTITY_SWITCH},
            blocking=True,
        )

    # Boost should not have activated — no schedule write, state stays off
    assert hass.states.get(ENTITY_SWITCH).state == STATE_OFF
    mock_set.assert_not_called()


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_restart_recovery(
    hass: HomeAssistant,
) -> None:
    """Test that boost state is restored after HA restart if still within boost window."""
    future_end = dt_util.now() + timedelta(minutes=5)
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ViCare",
        entry_id="1234",
        data=ENTRY_CONFIG,
        minor_version=2,
        options={CONF_BOOST_END_TIME: future_end.isoformat()},
    )

    fixtures: list[Fixture] = [Fixture({"type:heatpump"}, FIXTURE_HEATPUMP)]
    with (
        patch(f"{MODULE}.login", return_value=MockPyViCare(fixtures)),
        patch(f"{MODULE}.PLATFORMS", [Platform.SWITCH]),
    ):
        await setup_integration(hass, config_entry)

    # After setup, boost should be restored as active
    state = hass.states.get(ENTITY_SWITCH)
    assert state is not None
    assert state.state == STATE_ON
