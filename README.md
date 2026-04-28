# Maytronics One — Home Assistant Integration

Custom integration for Home Assistant to control **Maytronics One** pool robots (Dolphin EON series) via the cloud API.

> **Not compatible** with the MyDolphin Plus app. This integration targets the **Maytronics One** app exclusively.

---

## Supported devices

Any robot managed through the **Maytronics One** app (Android/iOS), including:
- Dolphin EON series (EON 30, EON 50, EON 120...)
- Robots with WiFi connectivity (device_type 65)

Tested on: **Dolphin EON 120d Europe**

---

## Features

| Entity | Type | Description |
|---|---|---|
| Nettoyage | Switch | Start / stop a cleaning cycle |
| Batterie | Sensor | Battery level (%) |
| Mode nettoyage | Sensor | Current cleaning mode |
| Statut | Sensor | idle / cleaning / error |
| Cycles nettoyage | Sensor | Total cleaning cycle count |
| En nettoyage | Binary sensor | Robot actively cleaning |
| En charge | Binary sensor | Robot charging |
| MQTT connecté | Binary sensor | Cloud connection status |
| Robot connecté | Binary sensor | Robot online in the cloud |

---

## Requirements

- Home Assistant 2024.1+
- A **Maytronics One** account with at least one registered robot
- Robot connected to WiFi (not BLE-only mode)
- Python packages installed automatically: `awsiotsdk`, `cryptography`

---

## Installation

### Via HACS (recommended)

1. In HACS, click **⋮ → Custom repositories**
2. Add `https://github.com/Jycreyn/maytronics-one-ha` as an **Integration**
3. Search for **Maytronics One** and install
4. Restart Home Assistant

### Manual

1. Copy `custom_components/maytronics_one/` into your HA `config/custom_components/` directory
2. Restart Home Assistant

---

## Configuration

1. Go to **Settings → Integrations → Add Integration**
2. Search for **Maytronics One**
3. Enter your Maytronics One account email
4. Check your inbox for the OTP code and enter it
5. Select your robot from the list

No password needed — authentication uses a one-time code sent by email (same as the app).

---

## How it works

- **Authentication**: AWS Cognito (CUSTOM_AUTH flow with OTP email)
- **Real-time state**: AWS IoT MQTT via WebSocket (named shadows: `Status`, `Config`, `Search`)
- **Commands**: Published to the `Status` named shadow desired state
- **Token refresh**: Automatic, no re-authentication needed

---

## Troubleshooting

### Integration not loading
Check that `awsiotsdk` and `cryptography` are installed in your HA environment:
```bash
docker exec homeassistant pip show awsiotsdk cryptography
```

### MQTT not connecting
Enable debug logging in `configuration.yaml`:
```yaml
logger:
  logs:
    custom_components.maytronics_one: debug
```

### Robot shows unavailable
The robot must be connected to WiFi and reachable from the Maytronics cloud. Verify it works in the Maytronics One app first.

---

## Known limitations

- Start/stop command payload may need adjustment depending on robot firmware — if the switch has no effect, check HA logs for the MQTT response on `shadow/name/Status/update/accepted`
- BLE-only robots are not supported (WiFi required for cloud connectivity)

---

## Contributing

Pull requests welcome. When testing, enable `debug` logging and share the MQTT topic/payload logs to help map new robot models.
