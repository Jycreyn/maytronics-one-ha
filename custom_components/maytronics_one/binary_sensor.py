"""Binary sensors: en nettoyage, en charge, connecté."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
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
        MaytronicsCleaningBinarySensor(coordinator, entry, device_info),
        MaytronicsChargingBinarySensor(coordinator, entry, device_info),
        MaytronicsConnectedBinarySensor(coordinator, entry, device_info),
        MaytronicsRobotConnectedBinarySensor(coordinator, entry, device_info),
    ])


class _BaseBinary(CoordinatorEntity[MaytronicsCoordinator], BinarySensorEntity):
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


class MaytronicsCleaningBinarySensor(_BaseBinary):
    _attr_name = "En nettoyage"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator, entry, device_info, "is_cleaning")

    @property
    def is_on(self):
        return self.coordinator.robot_state.is_cleaning

    @property
    def available(self):
        return self.coordinator.robot_state.connected


class MaytronicsChargingBinarySensor(_BaseBinary):
    _attr_name = "En charge"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator, entry, device_info, "is_charging")

    @property
    def is_on(self):
        return self.coordinator.robot_state.is_charging

    @property
    def available(self):
        return self.coordinator.robot_state.connected


class MaytronicsConnectedBinarySensor(_BaseBinary):
    _attr_name = "MQTT connecté"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator, entry, device_info, "connected")

    @property
    def is_on(self):
        return self.coordinator.robot_state.connected

    @property
    def available(self):
        return True  # toujours disponible — indique la connectivité MQTT


class MaytronicsRobotConnectedBinarySensor(_BaseBinary):
    _attr_name = "Robot connecté"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, entry, device_info):
        super().__init__(coordinator, entry, device_info, "robot_connected")

    @property
    def is_on(self):
        return self.coordinator.robot_state.robot_connected

    @property
    def available(self):
        return self.coordinator.robot_state.connected
