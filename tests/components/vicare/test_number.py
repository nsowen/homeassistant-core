"""Test ViCare number entity."""

from unittest.mock import patch

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.vicare.const import (
    CONF_CIRCULATION_BOOST_DURATION,
    DEFAULT_CIRCULATION_BOOST_DURATION,
    DOMAIN,
)
from homeassistant.components.vicare.number import (
    CIRCULATION_BOOST_DURATION_DESCRIPTION,
    ViCareNumber,
)
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import ENTRY_CONFIG, MODULE, setup_integration
from .conftest import Fixture, MockPyViCare

from tests.common import MockConfigEntry, snapshot_platform


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_all_entities(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test all entities."""
    fixtures: list[Fixture] = [Fixture({"type:boiler"}, "vicare/Vitodens300W.json")]
    with (
        patch(f"{MODULE}.login", return_value=MockPyViCare(fixtures)),
        patch(f"{MODULE}.PLATFORMS", [Platform.NUMBER]),
    ):
        await setup_integration(hass, mock_config_entry)

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_duration_not_created_when_feature_unsupported(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test that circulation boost duration is NOT created when feature is not supported."""
    fixtures: list[Fixture] = [Fixture({"type:boiler"}, "vicare/Vitodens300W.json")]

    with (
        patch(f"{MODULE}.login", return_value=MockPyViCare(fixtures)),
        patch(f"{MODULE}.PLATFORMS", [Platform.NUMBER]),
    ):
        await setup_integration(hass, mock_config_entry)

    # The mock device doesn't support getDomesticHotWaterCirculationSchedule
    # so the circulation boost duration number should NOT be created
    entity = entity_registry.async_get(
        "number.vitodens300w_dhw_circulation_boost_duration"
    )
    assert entity is None


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_duration_entity_class_structure(
    hass: HomeAssistant,
) -> None:
    """Test that the ViCareNumber class has the required methods for circulation boost duration."""
    # Verify the description exists
    assert CIRCULATION_BOOST_DURATION_DESCRIPTION is not None
    assert (
        CIRCULATION_BOOST_DURATION_DESCRIPTION.key == "dhw_circulation_boost_duration"
    )
    assert CIRCULATION_BOOST_DURATION_DESCRIPTION.entity_category is not None
    assert CIRCULATION_BOOST_DURATION_DESCRIPTION.native_min_value == 1
    assert CIRCULATION_BOOST_DURATION_DESCRIPTION.native_max_value == 60

    # Verify ViCareNumber has the required methods
    assert hasattr(ViCareNumber, "async_set_native_value")
    assert hasattr(ViCareNumber, "native_value")


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_duration_default(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that circulation boost duration defaults to DEFAULT_CIRCULATION_BOOST_DURATION."""
    fixtures: list[Fixture] = [Fixture({"type:heatpump"}, "vicare/Vitocal250A.json")]
    with (
        patch(f"{MODULE}.login", return_value=MockPyViCare(fixtures)),
        patch(f"{MODULE}.PLATFORMS", [Platform.NUMBER]),
    ):
        await setup_integration(hass, mock_config_entry)

    state = hass.states.get("number.model0_dhw_circulation_boost_duration")
    assert state is not None
    assert float(state.state) == DEFAULT_CIRCULATION_BOOST_DURATION


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_circulation_boost_duration_set(
    hass: HomeAssistant,
) -> None:
    """Test that setting the duration persists it to config entry options."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="ViCare",
        entry_id="1234",
        data=ENTRY_CONFIG,
        minor_version=2,
    )

    fixtures: list[Fixture] = [Fixture({"type:heatpump"}, "vicare/Vitocal250A.json")]
    with (
        patch(f"{MODULE}.login", return_value=MockPyViCare(fixtures)),
        patch(f"{MODULE}.PLATFORMS", [Platform.NUMBER]),
    ):
        await setup_integration(hass, config_entry)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {
            ATTR_ENTITY_ID: "number.model0_dhw_circulation_boost_duration",
            ATTR_VALUE: 15,
        },
        blocking=True,
    )

    assert config_entry.options.get(CONF_CIRCULATION_BOOST_DURATION) == 15
    state = hass.states.get("number.model0_dhw_circulation_boost_duration")
    assert float(state.state) == 15
