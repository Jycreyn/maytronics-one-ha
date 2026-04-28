"""Switch: démarrer / arrêter le nettoyage."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ROBOT_NAME, CONF_ROBOT_SERNUM, CONF_ROBOT_UUID, DOMAIN
from .coordinator import MaytronicsCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MaytronicsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MaytronicsCleaningSwitch(coordinator, entry)])


class MaytronicsCleaningSwitch(CoordinatorEntity[MaytronicsCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Nettoyage"

    def __init__(self, coordinator: MaytronicsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        uuid = entry.data[CONF_ROBOT_UUID]
        self._attr_unique_id = f"{uuid}_cleaning"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, uuid)},
            name=f"Dolphin {entry.data[CONF_ROBOT_SERNUM]}",
            manufacturer="Maytronics",
            model=entry.data.get(CONF_ROBOT_NAME, "Dolphin"),
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.robot_state.is_cleaning

    @property
    def available(self) -> bool:
        return self.coordinator.robot_state.connected

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_start_cleaning()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_stop_cleaning()
