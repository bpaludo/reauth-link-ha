"""Constants for Reauth Link."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "reauth_link"

# Integrations whose sign-in (a Spotify refresh token) expires after 6 months.
# Any other one needs its own proof before it goes in here.
SUPPORTED_DOMAINS: tuple[str, ...] = ("spotify", "spotifyplus")

# Registered by each user in their Spotify app: https://<home assistant> + this path.
# Never change it: everyone would have to register the new one.
CALLBACK_PATH: Final = "/api/reauth_link/callback"

# The official Spotify integration cannot reauthenticate before this version.
MIN_HA_VERSION: Final = "2026.9.3"

SUBENTRY_LOGIN: Final = "login"

# main entry: one address per Home Assistant
CONF_URL: Final = "url"
CONF_TIME: Final = "time"

# subentry: one per login
CONF_LOGIN: Final = "login"
CONF_DOMAIN: Final = "domain"
CONF_ENTRY_ID: Final = "entry_id"
CONF_ACCOUNT: Final = "account"
CONF_ACCOUNT_NAME: Final = "account_name"
CONF_OWNER: Final = "owner"
CONF_ADMIN: Final = "admin"
CONF_DAYS: Final = "days"

DEFAULT_TIME: Final = "19:00:00"
DEFAULT_DAYS: Final = 180
MIN_DAYS: Final = 60
MAX_DAYS: Final = 365

NOTIFY_BEFORE_DAYS: Final = 30
REPEAT_DAYS: Final = 7
ESCALATE_BEFORE_DAYS: Final = 7
LINK_VALIDITY: Final = timedelta(days=60)
LINK_MARGIN: Final = timedelta(days=30)  # never hand out a link about to expire
MAX_ATTEMPTS: Final = 10  # different failed codes before a link dies
MAX_TRIES: Final = 3  # exchanges of the very same code
NOTIFY_TIMEOUT: Final = 30  # seconds, per notify action
SETTLE_TIMEOUT: Final = 20  # seconds, for the target to reload before the page answers

SIGNAL_UPDATED: Final = f"{DOMAIN}_updated"
EVENT_FINISHED: Final = f"{DOMAIN}_finished"
SERVICE_GENERATE_LINK: Final = "generate_link"
