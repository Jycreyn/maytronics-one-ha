"""Maytronics One API client — Cognito auth + REST API."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import aiohttp
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .const import (
    API_BASE,
    APP_KEY,
    COGNITO_CLIENT_ID,
    COGNITO_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)

COGNITO_HEADERS = {
    "Content-Type": "application/x-amz-json-1.1",
}
API_HEADERS = {
    "AppKey": APP_KEY,
    "Content-Type": "application/x-www-form-urlencoded",
}


@dataclass
class AuthTokens:
    id_token: str
    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds


@dataclass
class RobotDevice:
    sernum: str
    esernum: str
    uuid: str
    name: str
    is_ble: bool
    connect_via: str
    ble_address: str | None = None


@dataclass
class IotCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expires_at: float


class MaytronicsAuthError(Exception):
    pass


class MaytronicsApiClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    @staticmethod
    def _encrypt_esernum(esernum: str, email: str) -> str:
        """Encrypt eSernum exactly like the Android app before getToken."""
        if len(email) < 2:
            raise MaytronicsAuthError("Email too short for Maytronics eSernum encryption")

        key = hashlib.md5(f"{email[:2].lower()}ha".encode("utf-8")).digest()
        iv = os.urandom(16)
        padder = padding.PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(esernum.encode("utf-8")) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        return base64.b64encode(iv + encrypted).decode("utf-8")

    # ── Cognito auth ──────────────────────────────────────────────────────────

    async def initiate_otp(self, email: str) -> str:
        """Trigger OTP email. Returns the Cognito session string."""
        payload = {
            "AuthFlow": "CUSTOM_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"USERNAME": email},
        }
        resp = await self._cognito_post(
            "AWSCognitoIdentityProviderService.InitiateAuth", payload
        )
        if "ChallengeName" not in resp:
            raise MaytronicsAuthError(f"Unexpected Cognito response: {resp}")
        return resp["Session"]

    async def respond_to_otp(
        self, cognito_username: str, otp: str, session: str
    ) -> AuthTokens:
        """Validate OTP and return tokens. cognito_username = UUID from challenge."""
        payload = {
            "ChallengeName": "CUSTOM_CHALLENGE",
            "ClientId": COGNITO_CLIENT_ID,
            "ChallengeResponses": {
                "USERNAME": cognito_username,
                "ANSWER": otp,
            },
            "Session": session,
        }
        resp = await self._cognito_post(
            "AWSCognitoIdentityProviderService.RespondToAuthChallenge", payload
        )
        auth = resp.get("AuthenticationResult")
        if not auth:
            raise MaytronicsAuthError(f"OTP rejected: {resp}")
        return AuthTokens(
            id_token=auth["IdToken"],
            access_token=auth["AccessToken"],
            refresh_token=auth["RefreshToken"],
            expires_at=time.time() + auth.get("ExpiresIn", 3600) - 60,
        )

    async def initiate_otp_get_username(self, email: str) -> tuple[str, str]:
        """Initiate OTP and return (cognito_username, session)."""
        payload = {
            "AuthFlow": "CUSTOM_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"USERNAME": email},
        }
        resp = await self._cognito_post(
            "AWSCognitoIdentityProviderService.InitiateAuth", payload
        )
        if "ChallengeName" not in resp:
            raise MaytronicsAuthError(f"Unexpected Cognito response: {resp}")
        cognito_username = resp["ChallengeParameters"].get("USERNAME", email)
        return cognito_username, resp["Session"]

    async def refresh_tokens(self, refresh_token: str) -> AuthTokens:
        """Refresh without OTP."""
        payload = {
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"REFRESH_TOKEN": refresh_token},
        }
        resp = await self._cognito_post(
            "AWSCognitoIdentityProviderService.InitiateAuth", payload
        )
        auth = resp.get("AuthenticationResult")
        if not auth:
            raise MaytronicsAuthError(f"Refresh failed: {resp}")
        return AuthTokens(
            id_token=auth["IdToken"],
            access_token=auth["AccessToken"],
            # Refresh token is not returned on refresh — keep the old one
            refresh_token=refresh_token,
            expires_at=time.time() + auth.get("ExpiresIn", 3600) - 60,
        )

    async def _cognito_post(self, target: str, payload: dict) -> dict:
        headers = {**COGNITO_HEADERS, "X-Amz-Target": target}
        async with self._session.post(
            COGNITO_ENDPOINT, headers=headers, json=payload
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                raise MaytronicsAuthError(
                    f"Cognito error {resp.status}: {data}"
                )
            return data

    # ── Maytronics REST API ──────────────────────────────────────────────────

    async def get_user_devices(self, id_token: str) -> list[RobotDevice]:
        """Return list of robots linked to the account."""
        data = await self._api_get("/mobapi/user/v2/user-devices/", id_token)
        _LOGGER.info("user-devices raw: %s", data)
        robots = []
        for item in data:
            robots.append(
                RobotDevice(
                    sernum=item["SERNUM"],
                    esernum=item["eSERNUM"],
                    uuid=item["UUID"],
                    name=item.get("device_name", "Dolphin"),
                    is_ble=item.get("is_ble", False),
                    connect_via=item.get("connectVia", ""),
                    ble_address=item.get("ble_address"),
                )
            )
        return robots

    async def get_iot_credentials(
        self, id_token: str, esernum: str, email: str
    ) -> IotCredentials:
        """Get temporary AWS IoT credentials for MQTT connection."""
        headers = {
            "authorization": f"Bearer {id_token}",
            "AppKey": APP_KEY,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        encrypted_esernum = self._encrypt_esernum(esernum, email)
        async with self._session.post(
            f"{API_BASE}/mt-sso/aws/v2/getToken",
            headers=headers,
            data={"eSernum": encrypted_esernum},
        ) as resp:
            raw = await resp.json(content_type=None)

        data = raw.get("Data")
        data_fields = list(data.keys()) if isinstance(data, dict) else None
        _LOGGER.info(
            "getToken response: status=%s alert=%s fields=%s",
            raw.get("Status"),
            raw.get("Alert"),
            data_fields,
        )

        if raw.get("Status") not in ("1", 1, "200", 200):
            raise MaytronicsAuthError(
                f"getToken failed: {raw.get('Alert', raw)}"
            )

        data = raw["Data"]
        # Log all fields to discover the response structure on first real run
        _LOGGER.info(
            "IoT token fields available: %s", list(data.keys())
        )

        # Try to extract endpoint from response (field name TBD via logging)
        endpoint = data.get("endpointAddress") or data.get("endpoint") or data.get("iotEndpoint")
        if endpoint:
            _LOGGER.info("IoT endpoint from API: %s", endpoint)

        expiry_ms = data.get("TokenExpirationMilliseconds")
        if expiry_ms:
            expires_at = int(expiry_ms) / 1000.0
        else:
            expires_at = time.time() + 3600 - 60

        return IotCredentials(
            access_key_id=data["AccessKeyId"],
            secret_access_key=data["SecretAccessKey"],
            session_token=data["Token"],
            expires_at=expires_at,
        )

    async def authenticate_user(self, id_token: str) -> dict[str, Any]:
        """Validate auth and return user info."""
        return await self._api_post("/mobapi/user/v2/authenticate-user/", id_token)

    async def _api_get(self, path: str, id_token: str) -> Any:
        headers = {
            "authorization": f"Bearer {id_token}",
            "AppKey": APP_KEY,
        }
        async with self._session.get(
            f"{API_BASE}{path}", headers=headers
        ) as resp:
            raw = await resp.json(content_type=None)
        if resp.status >= 400:
            raise MaytronicsAuthError(f"API error {resp.status}: {raw}")
        if isinstance(raw, dict) and raw.get("Status") not in (None, "1", 1):
            raise MaytronicsAuthError(f"API error: {raw}")
        # Handle both {"Status":"1","Data":[...]} and direct list
        if isinstance(raw, dict) and "Data" in raw:
            return raw["Data"]
        return raw

    async def _api_post(self, path: str, id_token: str, data: dict | None = None) -> Any:
        headers = {
            "authorization": f"Bearer {id_token}",
            "AppKey": APP_KEY,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        async with self._session.post(
            f"{API_BASE}{path}", headers=headers, data=data or {}
        ) as resp:
            raw = await resp.json(content_type=None)
        if resp.status >= 400:
            raise MaytronicsAuthError(f"API error {resp.status}: {raw}")
        return raw
