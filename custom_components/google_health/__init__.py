"""The Google Health (Fit Sleep) integration.

Data source: Google Health API v4 (health.googleapis.com).
Reads sleep data points and aggregates per-night totals (light / deep / REM /
awake) for the last completed night, exposing them as the same sensor keys
as the original actstorms integration so dashboards keep working.

Note: v4 tokens must NOT contain fitness.* scopes (403 DISALLOWED_OAUTH_SCOPES).
"""
from __future__ import annotations

import datetime
import json
from typing import Any
import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client, config_entry_oauth2_flow
from homeassistant.helpers.config_entry_oauth2_flow import (
    ImplementationUnavailableError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    LOGGER,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    HEALTH_API_BASE,
)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Google Health component."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Google Health from a config entry."""
    try:
        implementation = (
            await config_entry_oauth2_flow.async_get_config_entry_implementation(
                hass, entry
            )
        )
    except ImplementationUnavailableError as err:
        raise ConfigEntryNotReady("OAuth2 implementation unavailable") from err

    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    # Initialize Coordinator
    coordinator = GoogleHealthDataUpdateCoordinator(hass, session)

    # Fetch first data batch
    await coordinator.async_config_entry_first_refresh()

    # Save coordinator to hass data
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Set up platforms (Sensor and Binary Sensor)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class GoogleHealthAPIClient:
    """API Client for the Google Health API (v4)."""

    def __init__(self, hass: HomeAssistant, oauth_session: config_entry_oauth2_flow.OAuth2Session) -> None:
        """Initialize the API client."""
        self.hass = hass
        self.oauth_session = oauth_session
        self.client_session = aiohttp_client.async_get_clientsession(hass)

    async def _request(self, method: str, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make an authenticated HTTP request with a 10s timeout."""
        await self.oauth_session.async_ensure_token_valid()

        token = self.oauth_session.token["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        timeout = aiohttp.ClientTimeout(total=10)

        async with self.client_session.request(
            method, url, headers=headers, params=params, timeout=timeout
        ) as response:
            body_text = await response.text()
            if response.status >= 400:
                LOGGER.warning(
                    "Google Health API error: %s %s -> HTTP %s body=%s",
                    method, url, response.status, body_text[:1000],
                )
            response.raise_for_status()
            return json.loads(body_text) if body_text else {}

    async def _safe_request(self, method: str, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Safely make request, logging warnings on failure."""
        try:
            return await self._request(method, url, params)
        except Exception as err:
            LOGGER.warning("Failed to fetch Google endpoint '%s': %s", url, err)
            return {}

    async def get_sleep(self) -> dict[str, Any]:
        """Retrieve sleep data points from Google Health API v4."""
        return await self._safe_request(
            "GET",
            f"{HEALTH_API_BASE}/users/me/dataTypes/sleep/dataPoints",
            params={"pageSize": 10},
        )


class GoogleHealthDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage polling Google Health v4 sleep data."""

    def __init__(
        self,
        hass: HomeAssistant,
        oauth_session: config_entry_oauth2_flow.OAuth2Session,
    ) -> None:
        """Initialize the coordinator."""
        self.client = GoogleHealthAPIClient(hass, oauth_session)
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=datetime.timedelta(seconds=DEFAULT_UPDATE_INTERVAL_SECONDS),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch sleep data from Google Health v4."""
        try:
            resp = await self.client.get_sleep()
            dps = resp.get("dataPoints") if isinstance(resp, dict) else None
            if not isinstance(dps, list) or not dps:
                LOGGER.warning("v4 returned no sleep dataPoints")
                return self._empty_payload()
            LOGGER.warning("v4 sleep dataPoints returned: %d", len(dps))
            return self._parse_v4_sleep(dps)
        except Exception as err:
            raise UpdateFailed(f"Failed to fetch sleep data: {err}") from err

    def _empty_payload(self) -> dict[str, Any]:
        return {
            "sleep_duration": 0.0,
            "sleep_deep": 0.0,
            "sleep_rem": 0.0,
            "sleep_light": 0.0,
            "sleeping": False,
            "sleep_phase": "AWAKE",
            "sleep_minutes_awake": 0,
            "sleep_minutes_asleep": 0,
            "sleep_time_to_fall_asleep": 0,
            "sleep_efficiency": 0.0,
            "sleep_deep_minutes": 0,
            "sleep_rem_minutes": 0,
            "sleep_light_minutes": 0,
            "sleep_device": "unknown",
            "sleep_manufacturer": "unknown",
            "sleep_start": None,
            "sleep_end": None,
        }

    def _parse_v4_sleep(self, dps: list[dict[str, Any]]) -> dict[str, Any]:
        """Parse Google Health v4 sleep dataPoints into the shared payload."""
        data = self._empty_payload()

        if not dps:
            return data

        latest_dp = dps[0]
        src = latest_dp.get("dataSource", {}) if isinstance(latest_dp.get("dataSource"), dict) else {}
        device = src.get("displayName") or src.get("manufacturer") or "unknown"
        manufacturer = src.get("manufacturer", "unknown")

        sleep_obj = latest_dp.get("sleep")
        if not isinstance(sleep_obj, dict):
            LOGGER.warning("v4 sleep dataPoint has no 'sleep' object: %s",
                           json.dumps(latest_dp)[:500])
            return data

        summary = sleep_obj.get("summary")
        if isinstance(summary, dict):
            minutes_asleep = int(summary.get("minutesAsleep", 0) or 0)
            minutes_awake = int(summary.get("minutesAwake", 0) or 0)
            period = int(summary.get("minutesInSleepPeriod", 0) or 0)

            deep_m = 0
            rem_m = 0
            light_m = 0
            for stage in (summary.get("stagesSummary") or []):
                stage_type = stage.get("type")
                stage_mins = int(stage.get("minutes", 0) or 0)
                if stage_type == "DEEP":
                    deep_m += stage_mins
                elif stage_type == "REM":
                    rem_m += stage_mins
                elif stage_type == "LIGHT":
                    light_m += stage_mins

            if light_m == 0 and deep_m + rem_m > 0:
                light_m = max(0, minutes_asleep - deep_m - rem_m)

            efficiency = round(minutes_asleep / period * 100.0, 1) if period else 0.0

            data.update({
                "sleep_duration": round(minutes_asleep / 60.0, 1),
                "sleep_deep": round(deep_m / 60.0, 1),
                "sleep_rem": round(rem_m / 60.0, 1),
                "sleep_light": round(light_m / 60.0, 1),
                "sleep_minutes_awake": minutes_awake,
                "sleep_minutes_asleep": minutes_asleep,
                "sleep_efficiency": efficiency,
                "sleep_deep_minutes": deep_m,
                "sleep_rem_minutes": rem_m,
                "sleep_light_minutes": light_m,
                "sleep_device": device,
                "sleep_manufacturer": manufacturer,
            })

        # Start/end + currently-sleeping detection from interval
        interval = sleep_obj.get("interval")
        if isinstance(interval, dict) and "startTime" in interval and "endTime" in interval:
            try:
                def parse_iso(s: str) -> datetime.datetime:
                    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))

                st = parse_iso(interval["startTime"])
                en = parse_iso(interval["endTime"])
                data["sleep_start"] = dt_util.as_local(st).isoformat()
                data["sleep_end"] = dt_util.as_local(en).isoformat()

                now_utc = datetime.datetime.now(datetime.timezone.utc)
                if st <= now_utc < en:
                    data["sleeping"] = True
                    data["sleep_phase"] = "ASLEEP"
            except (ValueError, TypeError):
                pass

        return data
