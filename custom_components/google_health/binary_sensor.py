"""Binary sensor platform for Google Health integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from . import GoogleHealthDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Google Health binary sensors from a config entry."""
    coordinator: GoogleHealthDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    binary_sensors = [
        GoogleHealthSleepingBinarySensor(coordinator, entry.entry_id),
    ]

    async_add_entities(binary_sensors)


class GoogleHealthSleepingBinarySensor(
    CoordinatorEntity[GoogleHealthDataUpdateCoordinator], BinarySensorEntity
):
    """Representation of the Sleeping Status binary sensor."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:sleep"

    def __init__(
        self,
        coordinator: GoogleHealthDataUpdateCoordinator,
        entry_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._attr_name = "Sleeping"
        self._attr_unique_id = f"{entry_id}_sleeping"

    @property
    def is_on(self) -> bool:
        """Return True if the user is currently sleeping."""
        return self.coordinator.data.get("sleeping", False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes of the sleep sensor."""
        phase = self.coordinator.data.get("sleep_phase", "AWAKE")
        return {
            "sleep_phase": phase,
            "phase": phase,
        }

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device registry information."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.config_entry.entry_id)},
            "name": f"{self.coordinator.config_entry.title} Google Health Account",
            "manufacturer": "Google",
            "model": "Health API Connection",
        }
