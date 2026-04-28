DOMAIN = "maytronics_one"

# Cognito
COGNITO_REGION = "us-west-2"
COGNITO_USER_POOL_ID = "us-west-2_PKsEdCoP5"
COGNITO_CLIENT_ID = "4ed12eq01o6n0tl5f0sqmkq2na"
COGNITO_ENDPOINT = "https://cognito-idp.us-west-2.amazonaws.com/"

# API Maytronics
API_BASE = "https://apps.maytronics.com"
APP_KEY = "39AF9BF2-E906-4205-9368-EB3E16663ACE"

# AWS IoT
IOT_ENDPOINT = "a12rqfdx55bdbv-ats.iot.eu-west-1.amazonaws.com"
IOT_REGION = "eu-west-1"

# Config entry keys
CONF_EMAIL = "email"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ID_TOKEN = "id_token"
CONF_ROBOT_SERNUM = "sernum"
CONF_ROBOT_ESERNUM = "esernum"
CONF_ROBOT_UUID = "uuid"
CONF_ROBOT_NAME = "robot_name"
CONF_CERT_PATH = "cert_path"
CONF_PRIVATE_KEY_PATH = "private_key_path"

# Coordinator
UPDATE_INTERVAL_SECONDS = 30
TOKEN_EXPIRY_BUFFER_SECONDS = 300  # refresh 5 min avant expiry

# MQTT topics (format découvert au runtime — basé sur UUID)
# Le topic exact sera loggué au premier démarrage
MQTT_CLIENT_ID_PREFIX = "maytronics-ha"

# WAF bypass: apps.maytronics.com bloque les UA non-mobiles
API_USER_AGENT = "okhttp/4.12.0"

# X.509 MQTT auth. The Android app stores a keystore named
# MaytronicsIoTSDK_prodRelease; once extracted, place PEM files here.
DEFAULT_CERT_PATH = "/config/maytronics_one/device.pem.crt"
DEFAULT_PRIVATE_KEY_PATH = "/config/maytronics_one/private.pem.key"
