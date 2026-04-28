"""Config flow: email → OTP → sélection robot."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .api import MaytronicsApiClient, MaytronicsAuthError
from .const import (
    API_USER_AGENT,
    CONF_EMAIL,
    CONF_ID_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_ROBOT_ESERNUM,
    CONF_ROBOT_NAME,
    CONF_ROBOT_SERNUM,
    CONF_ROBOT_UUID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class MaytronicsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._email: str = ""
        self._cognito_username: str = ""
        self._cognito_session: str = ""
        self._id_token: str = ""
        self._refresh_token: str = ""
        self._robots: list[dict] = []
        self._client: MaytronicsApiClient | None = None

    def _get_client(self) -> MaytronicsApiClient:
        if self._client is None:
            session = aiohttp.ClientSession(headers={"User-Agent": API_USER_AGENT})
            self._client = MaytronicsApiClient(session)
        return self._client

    # ── Step 1: email ─────────────────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip()
            client = self._get_client()
            try:
                self._cognito_username, self._cognito_session = (
                    await client.initiate_otp_get_username(self._email)
                )
                return await self.async_step_otp()
            except MaytronicsAuthError as err:
                _LOGGER.error("OTP initiation failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected error during OTP init: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_EMAIL): str}
            ),
            errors=errors,
        )

    # ── Step 2: OTP ───────────────────────────────────────────────────────────

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            otp = user_input["otp"].strip()
            client = self._get_client()
            try:
                tokens = await client.respond_to_otp(
                    self._cognito_username, otp, self._cognito_session
                )
                self._id_token = tokens.id_token
                self._refresh_token = tokens.refresh_token
                return await self.async_step_select_robot()
            except MaytronicsAuthError as err:
                _LOGGER.error("OTP validation failed: %s", err)
                errors["base"] = "invalid_auth"
            except Exception as err:
                _LOGGER.exception("Unexpected error during OTP: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="otp",
            data_schema=vol.Schema({vol.Required("otp"): str}),
            errors=errors,
            description_placeholders={"email": self._email},
        )

    # ── Step 3: sélection robot ───────────────────────────────────────────────

    async def async_step_select_robot(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if not self._robots:
            client = self._get_client()
            try:
                devices = await client.get_user_devices(self._id_token)
                self._robots = [
                    {
                        "uuid": d.uuid,
                        "esernum": d.esernum,
                        "sernum": d.sernum,
                        "name": d.name,
                    }
                    for d in devices
                ]
            except Exception as err:
                _LOGGER.error("Cannot fetch robots: %s", err)
                errors["base"] = "cannot_connect"

        if user_input is not None and not errors:
            selected_uuid = user_input["robot"]
            robot = next((r for r in self._robots if r["uuid"] == selected_uuid), None)
            if robot:
                await self.async_set_unique_id(robot["uuid"])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Dolphin {robot['sernum']}",
                    data={
                        CONF_EMAIL: self._email,
                        CONF_ID_TOKEN: self._id_token,
                        CONF_REFRESH_TOKEN: self._refresh_token,
                        CONF_ROBOT_UUID: robot["uuid"],
                        CONF_ROBOT_ESERNUM: robot["esernum"],
                        CONF_ROBOT_SERNUM: robot["sernum"],
                        CONF_ROBOT_NAME: robot["name"],
                    },
                )

        if not self._robots and not errors:
            return self.async_abort(reason="no_robots")

        robot_options = {r["uuid"]: f"{r['name']} ({r['sernum']})" for r in self._robots}

        return self.async_show_form(
            step_id="select_robot",
            data_schema=vol.Schema(
                {vol.Required("robot"): vol.In(robot_options)}
            ),
            errors=errors,
        )

    # ── strings.json compat ───────────────────────────────────────────────────

    @staticmethod
    def async_get_options_flow(config_entry):  # type: ignore[override]
        return None
