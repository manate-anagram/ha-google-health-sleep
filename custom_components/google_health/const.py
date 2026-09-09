"""Constants for the Google Health (Google Fit) integration."""
import logging

DOMAIN = "google_health"

LOGGER = logging.getLogger(__package__)

# OAuth 2.0 Auth and Token Endpoints
OAUTH2_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH2_TOKEN = "https://oauth2.googleapis.com/token"

# Required OAuth scopes: both the legacy Fit API and the new Google Health (v4) API.
# One re-auth grants both; the integration tries v4 first and falls back to Fit.
# NOTE: Google Health v4 scopes end in ".readonly" (e.g. googlehealth.sleep.readonly).
SCOPES = [
    "https://www.googleapis.com/auth/fitness.sleep.read",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
]

# Google Fit API v1 base URL (legacy, supported until end of 2026)
FITNESS_API_BASE = "https://fitness.googleapis.com/fitness/v1/users/me"

# Google Health API v4 base URL (new, account-centric)
HEALTH_API_BASE = "https://health.googleapis.com/v4"

# Update interval (default to 15 minutes to avoid rate limit throttling)
DEFAULT_UPDATE_INTERVAL_SECONDS = 900
