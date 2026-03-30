"""Switch for ViCare."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from PyViCare.PyViCareDevice import Device as PyViCareDevice
from PyViCare.PyViCareDeviceConfig import PyViCareDeviceConfig
from PyViCare.PyViCareUtils import PyViCareNotSupportedFeatureError

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
import homeassistant.util.dt as dt_util

from .const import (
    CONF_BOOST_END_TIME,
    CONF_BOOST_ORIGINAL_SCHEDULE,
    CONF_CIRCULATION_BOOST_DURATION,
    DEFAULT_CIRCULATION_BOOST_DURATION,
    WEEKDAYS,
)
from .entity import ViCareEntity
from .types import ViCareConfigEntry, ViCareDevice, ViCareRequiredKeysMixin
from .utils import get_device_serial, is_supported

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


@dataclass(frozen=True)
class ViCareSwitchEntityDescription(SwitchEntityDescription, ViCareRequiredKeysMixin):
    """Describes ViCare switch entity."""


SWITCH_DESCRIPTIONS: tuple[ViCareSwitchEntityDescription, ...] = (
    ViCareSwitchEntityDescription(
        key="dhw_circulation_boost",
        translation_key="dhw_circulation_boost",
        entity_category=EntityCategory.CONFIG,
        value_getter=lambda api: api.getDomesticHotWaterCirculationSchedule(),
    ),
)


def _build_entities(
    device_list: list[ViCareDevice],
    config_entry: ViCareConfigEntry,
) -> list[ViCareCirculationBoostSwitch]:
    """Create ViCare switch entities for a device."""

    return [
        ViCareCirculationBoostSwitch(
            description,
            get_device_serial(device.api),
            device.config,
            device.api,
            config_entry,
        )
        for device in device_list
        for description in SWITCH_DESCRIPTIONS
        if is_supported(description.key, description.value_getter, device.api)
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ViCareConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the ViCare switch entities."""
    async_add_entities(
        await hass.async_add_executor_job(
            _build_entities,
            config_entry.runtime_data.devices,
            config_entry,
        )
    )


class ViCareCirculationBoostSwitch(ViCareEntity, SwitchEntity):
    """Representation of a ViCare circulation boost switch."""

    entity_description: ViCareSwitchEntityDescription

    _is_boosting: bool = False
    _original_schedule: dict[str, Any] | None = None
    _unsub_auto_off: Any | None = None

    def __init__(
        self,
        description: ViCareSwitchEntityDescription,
        device_serial: str | None,
        device_config: PyViCareDeviceConfig,
        device: PyViCareDevice,
        config_entry: ViCareConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(
            description.key,
            device_serial,
            device_config,
            device,
            config_entry=config_entry,
        )
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added to HA — restore boost state after restart."""
        await super().async_added_to_hass()

        assert self._config_entry is not None
        boost_end_str = self._config_entry.options.get(CONF_BOOST_END_TIME)
        if not boost_end_str:
            return

        try:
            boost_end = datetime.fromisoformat(boost_end_str)
            remaining = (boost_end - dt_util.now()).total_seconds()

            if remaining > 0:
                # Restore persisted original schedule
                self._original_schedule = self._config_entry.options.get(
                    CONF_BOOST_ORIGINAL_SCHEDULE
                )
                self._is_boosting = True
                self._unsub_auto_off = async_call_later(
                    self.hass, remaining, self._handle_auto_off
                )
                self.async_write_ha_state()
            else:
                # Boost has expired while HA was offline — restore original schedule
                await self._restore_original_schedule()
                self._clear_boost_options()
        except (ValueError, TypeError) as err:
            _LOGGER.debug("Could not restore boost state: %s", err)

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self._is_boosting

    def _floor_to_10min(self, dt: datetime) -> str:
        """Floor datetime to previous 10-minute boundary."""
        return dt.replace(
            minute=(dt.minute // 10) * 10, second=0, microsecond=0
        ).strftime("%H:%M")

    def _ceil_to_10min(self, dt: datetime) -> str:
        """Ceil datetime to next 10-minute boundary."""
        m = ((dt.minute + 9) // 10) * 10
        adjusted = dt.replace(second=0, microsecond=0) + timedelta(
            minutes=m - dt.minute
        )
        return adjusted.strftime("%H:%M")

    def _get_boost_duration(self) -> int:
        """Get boost duration in minutes from config entry options."""
        assert self._config_entry is not None
        return self._config_entry.options.get(
            CONF_CIRCULATION_BOOST_DURATION,
            DEFAULT_CIRCULATION_BOOST_DURATION,
        )

    def _clear_boost_options(self) -> None:
        """Remove persisted boost state from config entry options."""
        assert self._config_entry is not None
        options = {
            k: v
            for k, v in self._config_entry.options.items()
            if k not in (CONF_BOOST_END_TIME, CONF_BOOST_ORIGINAL_SCHEDULE)
        }
        self.hass.config_entries.async_update_entry(self._config_entry, options=options)

    async def _restore_original_schedule(self) -> None:
        """Restore the original circulation schedule."""
        assert self._config_entry is not None
        original = self._config_entry.options.get(CONF_BOOST_ORIGINAL_SCHEDULE)
        if original is not None:
            try:
                restore = {day: original[day] for day in WEEKDAYS if day in original}
                await self.hass.async_add_executor_job(
                    self._api.setDomesticHotWaterCirculationSchedule,
                    restore,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Failed to restore original schedule: %s", err)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on — start circulation boost."""
        try:
            duration = self._get_boost_duration()

            original_schedule = await self.hass.async_add_executor_job(
                self._api.getDomesticHotWaterCirculationSchedule
            )
            self._original_schedule = original_schedule

            now = dt_util.now()
            start_time = self._floor_to_10min(now)
            boost_end_dt = now + timedelta(minutes=duration)
            end_time = self._ceil_to_10min(boost_end_dt)
            today_weekday = WEEKDAYS[now.weekday()]

            # Check for conflicts with existing slots
            existing_slots = original_schedule.get(today_weekday, [])
            for slot in existing_slots:
                slot_start = slot.get("start", "")
                slot_end = slot.get("end", "")
                if (
                    slot_start <= start_time < slot_end
                    or slot_start < end_time <= slot_end
                ):
                    _LOGGER.warning(
                        "Boost slot %s–%s conflicts with existing slot %s–%s",
                        start_time,
                        end_time,
                        slot_start,
                        slot_end,
                    )
                    return

            boost_slot: dict[str, Any] = {
                "start": start_time,
                "end": end_time,
                "mode": "on",
                "position": 0,
            }
            # Deep-copy slots so we don't mutate the saved original_schedule,
            # then prepend the boost slot and reassign positions sequentially.
            today_slots = [dict(s) for s in existing_slots]
            today_slots.insert(0, boost_slot)
            for i, slot in enumerate(today_slots):
                slot["position"] = i
            # The setSchedule command only accepts day entries — strip metadata fields.
            new_schedule = {
                day: (
                    today_slots
                    if day == today_weekday
                    else list(original_schedule[day])
                )
                for day in WEEKDAYS
                if day in original_schedule
            }

            _LOGGER.debug("Setting circulation schedule: %s", new_schedule)
            await self.hass.async_add_executor_job(
                self._api.setDomesticHotWaterCirculationSchedule,
                new_schedule,
            )

            # Persist boost state for restart recovery
            assert self._config_entry is not None
            self.hass.config_entries.async_update_entry(
                self._config_entry,
                options={
                    **self._config_entry.options,
                    CONF_BOOST_END_TIME: boost_end_dt.isoformat(),
                    CONF_BOOST_ORIGINAL_SCHEDULE: original_schedule,
                },
            )

            self._is_boosting = True
            self.async_write_ha_state()

            self._unsub_auto_off = async_call_later(
                self.hass, duration * 60, self._handle_auto_off
            )

        except PyViCareNotSupportedFeatureError:
            _LOGGER.error(
                "Circulation schedule not supported for this device",
            )
            self._attr_available = False
            self.async_write_ha_state()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to turn on circulation boost: %s", err)
            self._is_boosting = False
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off — stop circulation boost."""
        if self._unsub_auto_off:
            self._unsub_auto_off()
            self._unsub_auto_off = None

        try:
            if self._original_schedule is not None:
                restore = {
                    day: self._original_schedule[day]
                    for day in WEEKDAYS
                    if day in self._original_schedule
                }
                await self.hass.async_add_executor_job(
                    self._api.setDomesticHotWaterCirculationSchedule,
                    restore,
                )
                self._original_schedule = None
        except PyViCareNotSupportedFeatureError:
            _LOGGER.error(
                "Circulation schedule not supported for this device",
            )
            self._attr_available = False
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to restore schedule on turn off: %s", err)
        finally:
            self._is_boosting = False
            self._original_schedule = None
            self._clear_boost_options()
            self.async_write_ha_state()

    @callback
    def _handle_auto_off(self, _now: datetime) -> None:
        """Schedule automatic turn-off via HA event loop."""
        self._unsub_auto_off = None
        self.hass.async_create_task(self.async_turn_off())

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._attr_available
