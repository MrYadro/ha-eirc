DOMAIN = "eirc_spb"

BASE_URL = "https://ikus.pesc.ru/api"

CONF_LOGIN = "login"
CONF_PASSWORD = "password"
CONF_VERIFICATION_TOKEN = "verification_token"
CONF_ACCOUNTS = "accounts"

DEFAULT_SCAN_INTERVAL_HOURS = 12
MIN_SCAN_INTERVAL_HOURS = 1

HEADER_CAPTCHA = "Captcha"
HEADER_CAPTCHA_NONE = "none"
HEADER_WITH_TOTP = "withTotp"
HEADER_AUTH_VERIFICATION = "Auth-Verification"

VERSION = "1.2.0"
USER_AGENT = f"home-assistant-eirc-spb/{VERSION}"
REQUEST_TIMEOUT_SECONDS = 30

ATTR_ACCOUNT_ID = "account_id"
ATTR_METER_ID = "meter_id"
ATTR_SCALE_ID = "scale_id"
