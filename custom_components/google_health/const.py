"""Constants for the Google Health (Google Fit) integration."""
import logging

DOMAIN = "google_health"

LOGGER = logging.getLogger(__package__)

# OAuth 2.0 Auth and Token Endpoints
OAUTH2_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH2_TOKEN = "https://oauth2.googleapis.com/token"

# Required OAuth scopes (Google Fit API v1 - sleep segment read)
SCOPES = [
    "https://www.googleapis.com/auth/fitness.sleep.read",
]

# Google Fit API v1 base URL
FITNESS_API_BASE = "https://fitness.googleapis.com/fitness/v1/users/me"

# Update interval (default to 15 minutes to avoid rate limit throttling)
DEFAULT_UPDATE_INTERVAL_SECONDS = 900
