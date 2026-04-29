"""Coordinator: refresh tokens + connexion MQTT IoT."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import IotCredentials, MaytronicsApiClient, MaytronicsAuthError
from .const import (
    API_USER_AGENT,
    CONF_CERT_PATH,
    CONF_EMAIL,
    CONF_ID_TOKEN,
    CONF_PRIVATE_KEY_PATH,
    CONF_REFRESH_TOKEN,
    CONF_ROBOT_ESERNUM,
    CONF_ROBOT_UUID,
    DEFAULT_CERT_PATH,
    DEFAULT_PRIVATE_KEY_PATH,
    DOMAIN,
    IOT_ENDPOINT,
    IOT_REGION,
    MQTT_CLIENT_ID_PREFIX,
    TOKEN_EXPIRY_BUFFER_SECONDS,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class RobotState:
    """Current state of the robot, populated from MQTT messages."""

    def __init__(self) -> None:
        self.is_cleaning: bool = False
        self.is_charging: bool = False
        self.battery_level: int | None = None
        self.clean_mode: str | None = None
        self.op_type: int | None = None
        self.cycle_time: int | None = None
        self.sm_state: int | None = None
        self.status_code: int | None = None
        self.error_code: int | None = None
        self.cycle_count: int | None = None
        self.robot_connected: bool | None = None
        # From Search shadow
        self.fw_sm: str | None = None
        self.fw_wifi: str | None = None
        # From Config shadow
        self.suction_power: int | None = None
        self.favorite_cleaning_type: int | None = None
        self.raw: dict[str, Any] = {}
        self.last_updated: float = 0.0
        self.connected: bool = False

    def update_from_mqtt(self, topic: str, payload: dict[str, Any]) -> None:
        self.raw[topic] = payload
        self.last_updated = time.time()

        if "isConnected" in payload:
            self.robot_connected = bool(payload["isConnected"])
        if "isCharging" in payload:
            self.is_charging = bool(payload["isCharging"])
        if "fault" in payload:
            self.error_code = int(payload["fault"])
        if "cleaningCycle" in payload:
            self.cycle_count = int(payload["cleaningCycle"])

        system_state = payload.get("systemState")
        if isinstance(system_state, dict):
            if "battery" in system_state:
                battery = int(system_state["battery"])
                if 0 <= battery <= 100:
                    self.battery_level = battery
            if "mu" in system_state:
                self.status_code = int(system_state["mu"])
            if "sm" in system_state:
                self.sm_state = int(system_state["sm"])
                # sm=0 confirmed idle, sm!=0 means robot is running a cycle
                self.is_cleaning = self.sm_state != 0

        cycle_info = payload.get("cycleInfo")
        if isinstance(cycle_info, dict):
            if "opMode" in cycle_info:
                op_mode = int(cycle_info["opMode"])
                self.clean_mode = f"op_mode_{op_mode}"
            if "opType" in cycle_info:
                self.op_type = int(cycle_info["opType"])
            if "time" in cycle_info:
                self.cycle_time = int(cycle_info["time"])

        # Search shadow: firmware versions
        versions = payload.get("versions")
        if isinstance(versions, dict):
            sm_ver = versions.get("sm", {})
            if isinstance(sm_ver, dict) and "swMajor" in sm_ver:
                self.fw_sm = f"{sm_ver['swMajor']}.{sm_ver['swMinor']}"
            if "wi-fi" in versions:
                self.fw_wifi = str(versions["wi-fi"])

        # Config shadow: user preferences
        eco = payload.get("eco")
        if isinstance(eco, dict):
            if "SuctionPower" in eco:
                self.suction_power = int(eco["SuctionPower"])
        if "favoriteCleaningType" in payload:
            self.favorite_cleaning_type = int(payload["favoriteCleaningType"])


class MaytronicsCoordinator(DataUpdateCoordinator):
    """Gère auth, polling REST, et push MQTT."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,  # push MQTT, pas de polling
        )
        self._entry = entry
        self._session: aiohttp.ClientSession | None = None
        self._client: MaytronicsApiClient | None = None
        self._id_token: str = entry.data[CONF_ID_TOKEN]
        self._refresh_token: str = entry.data[CONF_REFRESH_TOKEN]
        self._token_expires_at: float = 0.0  # force refresh au premier appel
        self._email: str = entry.data[CONF_EMAIL]
        self._esernum: str = entry.data[CONF_ROBOT_ESERNUM]
        self._uuid: str = entry.data[CONF_ROBOT_UUID]
        self._cert_path: str = (
            entry.options.get(CONF_CERT_PATH)
            or entry.data.get(CONF_CERT_PATH)
            or DEFAULT_CERT_PATH
        )
        self._private_key_path: str = (
            entry.options.get(CONF_PRIVATE_KEY_PATH)
            or entry.data.get(CONF_PRIVATE_KEY_PATH)
            or DEFAULT_PRIVATE_KEY_PATH
        )
        self._iot_creds: IotCredentials | None = None
        self._mqtt_connection: Any = None
        self._mqtt_task: asyncio.Task | None = None
        self.robot_state = RobotState()

    # ── Setup / teardown ───────────────────────────────────────────────────────

    async def async_setup(self) -> None:
        self._session = aiohttp.ClientSession(headers={"User-Agent": API_USER_AGENT})
        self._client = MaytronicsApiClient(self._session)
        await self._ensure_id_token()
        # Log user-devices to see current isReg and connectivity status
        try:
            devices = await self._client.get_user_devices(self._id_token)
            for d in devices:
                _LOGGER.info("Device at startup: SERNUM=%s UUID=%s is_ble=%s connect_via=%s",
                             d.sernum, d.uuid, d.is_ble, d.connect_via)
        except Exception as err:
            _LOGGER.warning("Could not fetch user-devices at startup: %s", err)
        await self._start_mqtt()

    async def async_teardown(self) -> None:
        if self._mqtt_task:
            self._mqtt_task.cancel()
            try:
                await self._mqtt_task
            except asyncio.CancelledError:
                pass
        await self._disconnect_mqtt()
        if self._session:
            await self._session.close()

    # ── Token management ──────────────────────────────────────────────────────

    async def _ensure_id_token(self) -> str:
        """Return a valid IdToken, refreshing if needed."""
        if time.time() < self._token_expires_at - TOKEN_EXPIRY_BUFFER_SECONDS:
            return self._id_token

        _LOGGER.debug("Refreshing Cognito tokens")
        try:
            tokens = await self._client.refresh_tokens(self._refresh_token)
        except MaytronicsAuthError as err:
            raise UpdateFailed(f"Token refresh failed: {err}") from err

        self._id_token = tokens.id_token
        self._refresh_token = tokens.refresh_token
        self._token_expires_at = tokens.expires_at

        # Persister le nouveau token dans l'entry
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                CONF_ID_TOKEN: self._id_token,
                CONF_REFRESH_TOKEN: self._refresh_token,
            },
        )
        return self._id_token

    async def _ensure_iot_creds(self) -> IotCredentials:
        """Return valid IoT credentials, refreshing if needed."""
        if (
            self._iot_creds is not None
            and time.time() < self._iot_creds.expires_at - TOKEN_EXPIRY_BUFFER_SECONDS
        ):
            return self._iot_creds

        _LOGGER.debug("Fetching IoT credentials for %s", self._esernum)
        id_token = await self._ensure_id_token()
        self._iot_creds = await self._client.get_iot_credentials(
            id_token, self._esernum, self._email
        )
        _LOGGER.info("IoT credentials obtained, expire at %s", self._iot_creds.expires_at)
        return self._iot_creds

    # ── MQTT ──────────────────────────────────────────────────────────────────

    async def _start_mqtt(self) -> None:
        """Start the MQTT connection in a background task."""
        self._mqtt_task = self.hass.async_create_background_task(
            self._mqtt_loop(), "maytronics_mqtt"
        )

    async def _mqtt_loop(self) -> None:
        """Persistent MQTT loop with reconnect."""
        while True:
            try:
                await self._connect_mqtt()
                # Wait until disconnected
                while self.robot_state.connected:
                    await asyncio.sleep(5)
                    # Check if IoT creds need refresh
                    if (
                        self._iot_creds
                        and time.time() > self._iot_creds.expires_at - TOKEN_EXPIRY_BUFFER_SECONDS
                    ):
                        _LOGGER.info("IoT creds expiring, reconnecting MQTT")
                        await self._disconnect_mqtt()
                        break
            except asyncio.CancelledError:
                return
            except Exception as err:
                _LOGGER.warning("MQTT error: %s — reconnecting in 30s", err)
                self.robot_state.connected = False

            await asyncio.sleep(30)

    async def _connect_mqtt(self) -> None:
        """Connect to AWS IoT MQTT."""
        try:
            from awsiot import mqtt_connection_builder
        except ImportError:
            _LOGGER.error("awsiotsdk not installed — MQTT disabled")
            return

        loop = asyncio.get_event_loop()

        def _on_connected(connection, callback_data):
            _LOGGER.info("MQTT connected to %s", IOT_ENDPOINT)
            loop.call_soon_threadsafe(setattr, self.robot_state, "connected", True)

        def _on_failed(connection, callback_data):
            _LOGGER.warning("MQTT connection failed: %s", callback_data)
            loop.call_soon_threadsafe(setattr, self.robot_state, "connected", False)

        def _on_closed(connection, callback_data):
            _LOGGER.info("MQTT connection closed")
            loop.call_soon_threadsafe(setattr, self.robot_state, "connected", False)

        def _on_message(topic, payload, **kwargs):
            _LOGGER.debug("MQTT [%s]: %s", topic, payload[:200] if payload else "")
            try:
                data = json.loads(payload)
            except Exception:
                data = {"raw": payload.decode("utf-8", errors="replace")}
            # Log every unique topic for discovery
            _LOGGER.info("MQTT topic received: %s | data keys: %s", topic, list(data.keys()) if isinstance(data, dict) else type(data))
            self.hass.loop.call_soon_threadsafe(
                self._handle_mqtt_message, topic, data
            )

        client_id = f"{MQTT_CLIENT_ID_PREFIX}-{self._uuid[:8]}"

        if self._has_mtls_files():
            _LOGGER.info(
                "Using X.509 MQTT auth with cert=%s key=%s",
                self._cert_path,
                self._private_key_path,
            )
            self._mqtt_connection = await loop.run_in_executor(
                None,
                lambda: mqtt_connection_builder.mtls_from_path(
                    endpoint=IOT_ENDPOINT,
                    cert_filepath=self._cert_path,
                    pri_key_filepath=self._private_key_path,
                    client_id=client_id,
                    clean_session=False,
                    keep_alive_secs=30,
                    on_connection_success=_on_connected,
                    on_connection_failure=_on_failed,
                    on_connection_closed=_on_closed,
                ),
            )
        else:
            _LOGGER.warning(
                "Maytronics X.509 cert/key not found (%s, %s); falling back to "
                "encrypted getToken AWS IoT credentials",
                self._cert_path,
                self._private_key_path,
            )
            self._mqtt_connection = await self._build_websocket_connection(
                mqtt_connection_builder,
                client_id,
                _on_connected,
                _on_failed,
                _on_closed,
            )

        connect_future = self._mqtt_connection.connect()
        await loop.run_in_executor(None, lambda: connect_future.result(timeout=15))

        # Souscrire aux topics du robot
        await self._subscribe_topics(_on_message)

    def _has_mtls_files(self) -> bool:
        """Return true when the Android-extracted PEM pair is available."""
        return os.path.isfile(self._cert_path) and os.path.isfile(self._private_key_path)

    async def _build_websocket_connection(
        self,
        mqtt_connection_builder,
        client_id: str,
        on_connected,
        on_failed,
        on_closed,
    ):
        """Build legacy SigV4 WebSocket connection from getToken credentials."""
        try:
            from awscrt import auth
        except ImportError:
            _LOGGER.error("awscrt not installed — getToken MQTT fallback disabled")
            raise

        creds = await self._ensure_iot_creds()
        credentials_provider = auth.AwsCredentialsProvider.new_static(
            creds.access_key_id,
            creds.secret_access_key,
            creds.session_token,
        )
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: mqtt_connection_builder.websockets_with_default_aws_signing(
                endpoint=IOT_ENDPOINT,
                region=IOT_REGION,
                credentials_provider=credentials_provider,
                client_id=client_id,
                on_connection_success=on_connected,
                on_connection_failure=on_failed,
                on_connection_closed=on_closed,
            ),
        )

    async def _subscribe_topics(self, on_message) -> None:
        """Subscribe to all relevant robot topics."""
        from awscrt import mqtt

        uuid = self._uuid
        # AWS IoT policy allows the robot shadow topics. Broad wildcards on
        # guessed Maytronics topics can close the connection.
        topics = [
            f"$aws/things/{uuid}/shadow/name/Status/get/accepted",
            f"$aws/things/{uuid}/shadow/name/Status/get/rejected",
            f"$aws/things/{uuid}/shadow/name/Status/update/accepted",
            f"$aws/things/{uuid}/shadow/name/Status/update/rejected",
            f"$aws/things/{uuid}/shadow/name/Config/get/accepted",
            f"$aws/things/{uuid}/shadow/name/Config/get/rejected",
            f"$aws/things/{uuid}/shadow/name/Search/get/accepted",
            f"$aws/things/{uuid}/shadow/name/Search/get/rejected",
            f"Maytronics/V2/{uuid}/APP",
        ]

        loop = asyncio.get_event_loop()
        for topic in topics:
            try:
                sub_future, _ = self._mqtt_connection.subscribe(
                    topic=topic,
                    qos=mqtt.QoS.AT_MOST_ONCE,
                    callback=on_message,
                )
                await loop.run_in_executor(None, lambda f=sub_future: f.result(timeout=5))
                _LOGGER.info("Subscribed to: %s", topic)
            except Exception as err:
                _LOGGER.debug("Cannot subscribe to %s: %s", topic, err)

        # Demander les named shadows immédiatement.
        for name in ("Status", "Config", "Search"):
            try:
                pub_future, _ = self._mqtt_connection.publish(
                    topic=f"$aws/things/{uuid}/shadow/name/{name}/get",
                    payload="{}",
                    qos=mqtt.QoS.AT_MOST_ONCE,
                )
                await loop.run_in_executor(None, lambda: pub_future.result(timeout=5))
                _LOGGER.info("Named shadow get request sent: %s", name)
            except Exception as err:
                _LOGGER.debug("Named shadow get failed for %s: %s", name, err)

    async def _disconnect_mqtt(self) -> None:
        if self._mqtt_connection:
            try:
                loop = asyncio.get_event_loop()
                disc_future = self._mqtt_connection.disconnect()
                await loop.run_in_executor(None, lambda: disc_future.result(timeout=5))
            except Exception:
                pass
            self._mqtt_connection = None
        self.robot_state.connected = False

    def _handle_mqtt_message(self, topic: str, data: dict[str, Any]) -> None:
        """Called in HA event loop on MQTT message arrival."""
        # Unwrap AWS IoT shadow format
        if isinstance(data, dict):
            state_data = (
                data.get("state", {}).get("reported", data.get("state", data))
                if "state" in data
                else data
            )
        else:
            state_data = data

        self.robot_state.update_from_mqtt(topic, state_data if isinstance(state_data, dict) else data)
        self.async_set_updated_data(self.robot_state)

    # ── Robot commands ────────────────────────────────────────────────────────

    async def async_start_cleaning(self) -> None:
        """Send start cleaning command."""
        # Named shadow desired state — format TBD when robot is on
        await self._send_command({"state": {"desired": {"command": "start"}}})

    async def async_stop_cleaning(self) -> None:
        """Send stop cleaning command."""
        await self._send_command({"state": {"desired": {"command": "stop"}}})

    async def _send_command(self, payload: dict) -> None:
        from awscrt import mqtt

        if not self._mqtt_connection or not self.robot_state.connected:
            raise Exception("MQTT not connected")
        uuid = self._uuid
        # Named shadow Status/update — the classic shadow returns 404 on this robot
        topic = f"$aws/things/{uuid}/shadow/name/Status/update"
        loop = asyncio.get_event_loop()
        pub_future, _ = self._mqtt_connection.publish(
            topic=topic,
            payload=json.dumps(payload),
            qos=mqtt.QoS.AT_LEAST_ONCE,
        )
        await loop.run_in_executor(None, lambda: pub_future.result(timeout=5))
        _LOGGER.info("Command sent to %s: %s", topic, payload)

    async def _async_update_data(self) -> RobotState:
        """Fallback polling (normalement le push MQTT suffit)."""
        return self.robot_state
