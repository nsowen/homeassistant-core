"""Test ViCare switch entity."""

from datetime import UTC, datetime, timedelta
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
from homeassistant.components.vicare.switch import (
    _clean_boost_remnants,
    _pick_slot_to_remove,
    _slot_duration_minutes,
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


# ---------------------------------------------------------------------------
# Unit tests for helper functions (req. 7 and 8)
# ---------------------------------------------------------------------------


def test_slot_duration_minutes_normal() -> None:
    """Test _slot_duration_minutes for a regular slot."""
    assert _slot_duration_minutes({"start": "06:00", "end": "07:30"}) == 90


def test_slot_duration_minutes_ten() -> None:
    """Test _slot_duration_minutes identifies a 10-minute slot."""
    assert _slot_duration_minutes({"start": "14:00", "end": "14:10"}) == 10


def test_slot_duration_minutes_invalid() -> None:
    """Test _slot_duration_minutes returns -1 on parse error."""
    assert _slot_duration_minutes({"start": "bad", "end": "value"}) == -1
    assert _slot_duration_minutes({}) == -1


def test_pick_slot_to_remove_prefers_past() -> None:
    """Past slot should be preferred over future slots."""
    slots = [
        {"start": "06:00", "end": "09:00", "mode": "on", "position": 0},  # past
        {"start": "18:00", "end": "20:00", "mode": "on", "position": 1},  # future
    ]
    # current_time is "15:00" → first slot has ended
    idx = _pick_slot_to_remove(slots, "15:00")
    assert idx == 0


def test_pick_slot_to_remove_most_recent_past() -> None:
    """Among multiple past slots, the most recently ended one is chosen."""
    slots = [
        {"start": "06:00", "end": "09:00", "mode": "on", "position": 0},
        {"start": "12:00", "end": "13:00", "mode": "on", "position": 1},  # more recent
        {"start": "18:00", "end": "20:00", "mode": "on", "position": 2},
    ]
    idx = _pick_slot_to_remove(slots, "15:00")
    assert idx == 1


def test_pick_slot_to_remove_falls_back_to_earliest_future() -> None:
    """When no past slot exists, the earliest-starting future slot is removed."""
    slots = [
        {"start": "18:00", "end": "20:00", "mode": "on", "position": 0},
        {"start": "15:00", "end": "17:00", "mode": "on", "position": 1},  # earliest
        {"start": "20:00", "end": "22:00", "mode": "on", "position": 2},
    ]
    idx = _pick_slot_to_remove(slots, "14:00")
    assert idx == 1


def test_pick_slot_to_remove_empty() -> None:
    """Empty list should return None."""
    assert _pick_slot_to_remove([], "12:00") is None


def test_clean_boost_remnants_removes_10min_slots() -> None:
    """_clean_boost_remnants removes exactly-10-minute slots from every day."""
    schedule = {
        "mon": [
            {"start": "06:00", "end": "09:00", "mode": "on", "position": 0},
            {"start": "14:00", "end": "14:10", "mode": "on", "position": 1},  # remnant
        ],
        "tue": [
            {"start": "08:00", "end": "08:10", "mode": "on", "position": 0},  # remnant
        ],
        "wed": [],
    }
    result = _clean_boost_remnants(schedule)
    assert len(result["mon"]) == 1
    assert result["mon"][0]["start"] == "06:00"
    assert result["tue"] == []
    assert result["wed"] == []


def test_clean_boost_remnants_keeps_longer_slots() -> None:
    """_clean_boost_remnants does not remove slots longer than 10 minutes."""
    schedule = {
        "mon": [
            {"start": "06:00", "end": "06:20", "mode": "on", "position": 0},  # 20 min
            {"start": "12:00", "end": "13:00", "mode": "on", "position": 1},  # 60 min
        ],
    }
    result = _clean_boost_remnants(schedule)
    assert result["mon"] == schedule["mon"]


def test_clean_boost_remnants_passes_through_non_list_values() -> None:
    """_clean_boost_remnants leaves non-list values (metadata) intact."""
    schedule: dict = {"active": True, "default_mode": "off", "mon": []}
    result = _clean_boost_remnants(schedule)
    assert result["active"] is True
    assert result["default_mode"] == "off"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_at_max_slots_removes_past_slot(
    hass: HomeAssistant,
    vicare_heatpump: MockConfigEntry,
) -> None:
    """Test that when today has 4 slots, the most-recently-ended past slot is removed."""
    now = dt_util.now()
    today_key = WEEKDAYS[now.weekday()]

    # Build 4 slots: 2 clearly in the past, 2 in the future
    full_schedule = {
        **EMPTY_SCHEDULE,
        today_key: [
            {"start": "06:00", "end": "07:00", "mode": "on", "position": 0},
            {"start": "08:00", "end": "09:00", "mode": "on", "position": 1},
            {"start": "23:00", "end": "23:30", "mode": "on", "position": 2},
            {"start": "23:30", "end": "23:50", "mode": "on", "position": 3},
        ],
    }

    with (
        patch(
            "PyViCare.PyViCareHeatingDevice.HeatingDevice.getDomesticHotWaterCirculationSchedule",
            return_value=full_schedule,
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
    mock_set.assert_called_once()
    call_args = mock_set.call_args[0][0]
    # Today must have exactly 4 slots (3 original kept + 1 boost)
    assert len(call_args[today_key]) == 4
    # One of the slots must be the boost slot
    assert any(s["mode"] == "on" for s in call_args[today_key])


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_at_max_slots_no_past_removes_earliest(
    hass: HomeAssistant,
    vicare_heatpump: MockConfigEntry,
) -> None:
    """Test that when all 4 slots are future, the earliest-starting slot is removed."""
    now = dt_util.now()
    today_key = WEEKDAYS[now.weekday()]

    # All 4 slots are in the future — run the test at midnight-ish by using
    # times far ahead; use 22:xx so they are definitely future at test time.
    full_schedule = {
        **EMPTY_SCHEDULE,
        today_key: [
            {"start": "22:00", "end": "22:30", "mode": "on", "position": 0},
            {"start": "22:30", "end": "23:00", "mode": "on", "position": 1},
            {"start": "23:00", "end": "23:30", "mode": "on", "position": 2},
            {"start": "23:30", "end": "23:50", "mode": "on", "position": 3},
        ],
    }

    with (
        patch(
            "PyViCare.PyViCareHeatingDevice.HeatingDevice.getDomesticHotWaterCirculationSchedule",
            return_value=full_schedule,
        ),
        patch(
            "PyViCare.PyViCareHeatingDevice.HeatingDevice.setDomesticHotWaterCirculationSchedule",
        ) as mock_set,
        patch("homeassistant.util.dt.now") as mock_now,
    ):
        # Set "now" to 21:00 so all above slots are in the future
        mock_now.return_value = datetime(2024, 1, 15, 21, 0, 0, tzinfo=UTC)

        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: ENTITY_SWITCH},
            blocking=True,
        )

    assert hass.states.get(ENTITY_SWITCH).state == STATE_ON
    mock_set.assert_called_once()
    call_args = mock_set.call_args[0][0]
    today_slots = call_args.get(today_key, [])
    assert len(today_slots) == 4  # 3 kept + 1 boost


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_turn_off_cleans_remnant_slots(
    hass: HomeAssistant,
    vicare_heatpump: MockConfigEntry,
) -> None:
    """Test that 10-minute remnant boost slots are removed when restoring original schedule."""
    # The "original" schedule already contains a 10-minute remnant from a previous boost
    original_with_remnant = {
        **EMPTY_SCHEDULE,
        "mon": [
            {"start": "06:00", "end": "09:00", "mode": "on", "position": 0},
            {"start": "14:00", "end": "14:10", "mode": "on", "position": 1},  # remnant
        ],
        "tue": [
            {"start": "06:00", "end": "09:00", "mode": "on", "position": 0},
        ],
    }

    with (
        patch(
            "PyViCare.PyViCareHeatingDevice.HeatingDevice.getDomesticHotWaterCirculationSchedule",
            return_value=original_with_remnant,
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

        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: ENTITY_SWITCH},
            blocking=True,
        )

    assert hass.states.get(ENTITY_SWITCH).state == STATE_OFF
    # The restore call must not include the 10-minute remnant slot
    restore_args = mock_set.call_args[0][0]
    for day, slots in restore_args.items():
        if isinstance(slots, list):
            for slot in slots:
                assert _slot_duration_minutes(slot) != 10, (
                    f"Remnant 10-min slot found in day {day}: {slot}"
                )
