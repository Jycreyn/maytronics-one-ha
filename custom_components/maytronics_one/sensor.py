"""Sensors: batterie, mode nettoyage, statut, cycles."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ROBOT_NAME, CONF_ROBOT_SERNUM, CONF_ROBOT_UUID, DOMAIN
from .coordinator import MaytronicsCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MaytronicsCoordinator = hass.data[DOMAIN][entry.entry_id]
    uuid = entry.data[CONF_ROBOT_UUID]
    device_info = DeviceInfo(
        identifiers={(DOMAIN, uuid)},
        name=f"Dolphin {entry.data[CONF_ROBOT_SERNUM]}",
        manufacturer="Maytronics",
        model=entry.data.get(CONF_ROBOT_NAME, "Dolphin"),
    )
    async_add_entities([
        MaytronicsBatterySensor(coordinator, entry, device_info),
        MaytronicsCleanModeSensor(coordinator, entry, device_info),
        MaytronicsStatusSensor(coordinator, entry, device_info),
        MaytronicsCycleCountSensor(coordinator, entry, device_info),
    ])


class _BaseSensor(CoordinatorEntity[MaytronicsCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MaytronicsCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        suffix: str,
    ) -> None:
        super().__init__(coordinator)
        uuid = entry.data[CONF_ROBOT_UUID]
        self._attr_unique_id = f"{uuid}_{suffix}"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return self.coordinator.robot_state.connected


class MaytronicsBatterySensor(_BaseSensor):
    _attr_name = "Batterie"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator, entry, device_info, "battery")

    @property
    def native_value(self):
        return self.coordinator.robot_state.battery_level


class MaytronicsCleanModeSensor(_BaseSensor):
    _attr_name = "Mode nettoyage"

    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator, entry, device_info, "clean_mode")

    @property
    def native_value(self):
        return self.coordinator.robot_state.clean_mode


class MaytronicsStatusSensor(_BaseSensor):
    _attr_name = "Statut"

    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator, entry, device_info, "status")

    @property
    def native_value(self):
        sc = self.coordinator.robot_state.status_code
        if self.coordinator.robot_state.robot_connected is False:
            return "offline"
        if self.coordinator.robot_state.error_code:
            return "error"
        if sc is None:
            return None
        mapping = {0: "idle", 1: "cleaning", 2: "lifting", 3: "error", 7: "idle"}
        return mapping.get(sc, f"unknown_{sc}")

    @property
    def extra_state_attributes(self):
        state = self.coordinator.robot_state
        return {
            "status_code": state.status_code,
            "error_code": state.error_code,
            "robot_connected": state.robot_connected,
            "last_updated": state.last_updated,
            "raw_topics": list(state.raw.keys()),
        }


class MaytronicsCycleCountSensor(_BaseSensor):
    _attr_name = "Cycles nettoyage"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator, entry, device_info, "cycle_count")

    @property
    def native_value(self):
        return self.coordinator.robot_state.cycle_count
