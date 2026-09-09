"""Constants for the Google Health (Fit Sleep) integration."""
import logging

DOMAIN = "google_health"

LOGGER = logging.getLogger(__package__)

# OAuth 2.0 Auth and Token Endpoints
OAUTH2_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH2_TOKEN = "https://oauth2.googleapis.com/token"

# Required OAuth scope: Google Health API v4 sleep read.
# NOTE: v4 scopes end in ".readonly" and DISALLOW fitness.* scopes in the same
# token (verified: mixed-scope tokens get 403 DISALLOWED_OAUTH_SCOPES).
SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
]

# Google Health API v4 base URL (new, account-centric)
HEALTH_API_BASE = "https://health.googleapis.com/v4"

# Update interval (default to 15 minutes to avoid rate limit throttling)
DEFAULT_UPDATE_INTERVAL_SECONDS = 900
