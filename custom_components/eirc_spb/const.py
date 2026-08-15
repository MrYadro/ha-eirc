DOMAIN = "eirc_spb"

BASE_URL = "https://ikus.pesc.ru/api"

CONF_LOGIN = "login"
CONF_PASSWORD = "password"
CONF_VERIFICATION_TOKEN = "verification_token"
CONF_ACCOUNTS = "accounts"

DEFAULT_SCAN_INTERVAL_HOURS = 12
MIN_SCAN_INTERVAL_HOURS = 1
DEFAULT_DEADLINE_DAYS = 3
CONF_PERSISTENT_NOTIFICATIONS = "persistent_notifications"
CONF_DEADLINE_DAYS = "deadline_days"

HEADER_CAPTCHA = "Captcha"
HEADER_CAPTCHA_NONE = "none"
HEADER_WITH_TOTP = "withTotp"
HEADER_AUTH_VERIFICATION = "Auth-Verification"

VERSION = "1.5.2"
USER_AGENT = f"home-assistant-eirc-spb/{VERSION}"
REQUEST_TIMEOUT_SECONDS = 30

ATTR_ACCOUNT_ID = "account_id"
ATTR_METER_ID = "meter_id"
ATTR_SCALE_ID = "scale_id"
