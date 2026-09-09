"""Sensor platform for Google Health (Google Fit) integration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from . import GoogleHealthDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class GoogleHealthSensorEntityDescription(SensorEntityDescription):
    """Class describing Google Health sensor entities."""

    value_fn: Callable[[dict[str, Any]], Any] | None = None
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    suggested_display_precision: int | None = None


SENSOR_DESCRIPTIONS: tuple[GoogleHealthSensorEntityDescription, ...] = (
    # Sleep Metrics (sourced from Google Fit sleep segments)
    GoogleHealthSensorEntityDescription(
        key="sleep_duration",
        name="Last Sleep Duration",
        native_unit_of_measurement="h",
        icon="mdi:sleep",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        attributes_fn=lambda data: {
            "source_device": data.get("sleep_device"),
            "source_manufacturer": data.get("sleep_manufacturer"),
            "minutes_asleep": data.get("sleep_minutes_asleep"),
            "minutes_awake": data.get("sleep_minutes_awake"),
            "light_sleep_minutes": data.get("sleep_light_minutes"),
            "deep_sleep_minutes": data.get("sleep_deep_minutes"),
            "rem_sleep_minutes": data.get("sleep_rem_minutes"),
            "sleep_efficiency_percent": data.get("sleep_efficiency"),
            "start_time": data.get("sleep_start"),
            "end_time": data.get("sleep_end"),
        },
    ),
    GoogleHealthSensorEntityDescription(
        key="sleep_deep",
        name="Last Deep Sleep Duration",
        native_unit_of_measurement="h",
        icon="mdi:sleep",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        attributes_fn=lambda data: {
            "source_device": data.get("sleep_device"),
            "deep_sleep_minutes": data.get("sleep_deep_minutes"),
        },
    ),
    GoogleHealthSensorEntityDescription(
        key="sleep_rem",
        name="Last REM Sleep Duration",
        native_unit_of_measurement="h",
        icon="mdi:sleep",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        attributes_fn=lambda data: {
            "source_device": data.get("sleep_device"),
            "rem_sleep_minutes": data.get("sleep_rem_minutes"),
        },
    ),
    GoogleHealthSensorEntityDescription(
        key="sleep_light",
        name="Last Light Sleep Duration",
        native_unit_of_measurement="h",
        icon="mdi:sleep",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        attributes_fn=lambda data: {
            "source_device": data.get("sleep_device"),
            "light_sleep_minutes": data.get("sleep_light_minutes"),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Google Health sensors from a config entry."""
    coordinator: GoogleHealthDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    sensors = [
        GoogleHealthSensor(coordinator, description, entry.entry_id)
        for description in SENSOR_DESCRIPTIONS
    ]

    async_add_entities(sensors)


class GoogleHealthSensor(CoordinatorEntity[GoogleHealthDataUpdateCoordinator], SensorEntity):
    """Representation of a Google Health sensor."""

    entity_description: GoogleHealthSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GoogleHealthDataUpdateCoordinator,
        description: GoogleHealthSensorEntityDescription,
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_suggested_display_precision = description.suggested_display_precision

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        if self.entity_description.value_fn:
            return self.entity_description.value_fn(self.coordinator.data)
        return self.coordinator.data.get(self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes for this sensor."""
        if self.entity_description.attributes_fn:
            return self.entity_description.attributes_fn(self.coordinator.data)
        return None

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device registry information."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.config_entry.entry_id)},
            "name": f"{self.coordinator.config_entry.title} Google Health Account",
            "manufacturer": "Google",
            "model": "Health API Connection",
        }
