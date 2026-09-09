"""The Google Health (Google Fit) integration.

Dual data source:
1. Google Health API v4 (health.googleapis.com) - new account-centric API.
   Preferred when data is available (Health Connect -> Google Health sync).
2. Google Fit API v1 (fitness.googleapis.com) - legacy, supported until end
   of 2026. Used as fallback when v4 has no data.

Reads sleep data and aggregates per-night totals (light / deep / REM / awake)
for the last completed night, exposing them as the same sensor keys as the
original actstorms integration so dashboards keep working.
"""
from __future__ import annotations

import datetime
import json
from typing import Any
import urllib.parse
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
    FITNESS_API_BASE,
    HEALTH_API_BASE,
)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

# Google Fit sleep segment intVal -> stage name
STAGE_NAMES = {0: "unknown", 1: "awake", 2: "sleep", 3: "out", 4: "light", 5: "deep", 6: "rem"}


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
    """API Client for Google Health v4 and Google Fit v1."""

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

    # ---- Google Health API v4 ----
    async def get_v4_sleep(self) -> dict[str, Any]:
        """Retrieve sleep data points from Google Health API v4."""
        return await self._safe_request(
            "GET",
            f"{HEALTH_API_BASE}/users/me/dataTypes/sleep/dataPoints",
            params={"pageSize": 5},
        )

    # ---- Google Fit API v1 ----
    async def get_data_sources(self) -> dict[str, Any]:
        """Retrieve all data sources."""
        return await self._safe_request("GET", f"{FITNESS_API_BASE}/dataSources")

    async def get_dataset(self, data_stream_id: str, start_nanos: int, end_nanos: int) -> dict[str, Any]:
        """Retrieve a dataset (points) for a data source between nanosecond timestamps."""
        encoded = urllib.parse.quote(data_stream_id, safe="")
        url = f"{FITNESS_API_BASE}/dataSources/{encoded}/datasets/{start_nanos}-{end_nanos}"
        return await self._safe_request("GET", url)


class GoogleHealthDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage polling sleep data (v4 preferred, Fit fallback)."""

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
        """Fetch sleep data (v4 first, then Fit fallback)."""
        try:
            # 1. Try Google Health API v4
            v4 = await self.client.get_v4_sleep()
            v4_dps = v4.get("dataPoints") if isinstance(v4, dict) else None
            if isinstance(v4_dps, list) and v4_dps:
                LOGGER.warning("Using Google Health v4 data (points=%d)", len(v4_dps))
                return self._parse_v4_sleep(v4_dps)

            # 2. Fallback: Google Fit API v1
            LOGGER.warning("v4 returned no data, falling back to Google Fit API")
            return await self._fetch_fit_sleep_summary()

        except Exception as err:
            raise UpdateFailed(f"Failed to fetch sleep data: {err}") from err

    # ------------------------------------------------------------------
    # v4 parsing
    # ------------------------------------------------------------------
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
            LOGGER.warning("v4 sleep dataPoint has no 'sleep' object")
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

            # Some responses only give DEEP+REM; light = asleep - deep - rem
            if light_m == 0 and deep_m + rem_m > 0:
                light_m = max(0, minutes_asleep - deep_m - rem_m)

            asleep_m = minutes_asleep
            efficiency = round(asleep_m / period * 100.0, 1) if period else 0.0

            data.update({
                "sleep_duration": round(asleep_m / 60.0, 1),
                "sleep_deep": round(deep_m / 60.0, 1),
                "sleep_rem": round(rem_m / 60.0, 1),
                "sleep_light": round(light_m / 60.0, 1),
                "sleep_minutes_awake": minutes_awake,
                "sleep_minutes_asleep": asleep_m,
                "sleep_efficiency": efficiency,
                "sleep_deep_minutes": deep_m,
                "sleep_rem_minutes": rem_m,
                "sleep_light_minutes": light_m,
                "sleep_device": device,
                "sleep_manufacturer": manufacturer,
            })

        # Start/end time from interval
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

    # ------------------------------------------------------------------
    # Fit API fallback parsing
    # ------------------------------------------------------------------
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

    async def _fetch_fit_sleep_summary(self) -> dict[str, Any]:
        """Aggregate sleep segments from Google Fit API into the data dict."""
        data = self._empty_payload()

        # 1. Find sleep segment data sources (prefer derived aggregate, then xiaomi raw)
        sources_resp = await self.client.get_data_sources()
        sources = sources_resp.get("dataSource", []) if isinstance(sources_resp, dict) else []
        sleep_sources = [
            s for s in sources
            if s.get("dataType", {}).get("name") == "com.google.sleep.segment"
        ]
        if not sleep_sources:
            LOGGER.warning("No com.google.sleep.segment data source found in Google Fit")
            return data

        # Prefer derived, then raw xiaomi
        def sort_key(s: dict[str, Any]) -> int:
            sid = s.get("dataStreamId", "")
            if sid.startswith("derived:"):
                return 0
            if "xiaomi" in sid.lower():
                return 1
            return 2

        sleep_sources.sort(key=sort_key)
        source = sleep_sources[0]
        stream_id = source.get("dataStreamId", "")
        app_pkg = source.get("application", {}).get("packageName", "unknown")
        device = source.get("device", {}).get("model", app_pkg)
        manufacturer = source.get("device", {}).get("manufacturer", "unknown")

        # 2. Fetch segments for the last ~2 days so we always cover the previous night
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_ns = int(now_utc.timestamp() * 1e9)
        start_ns = now_ns - 2 * 86400 * 10**9

        ds = await self.client.get_dataset(stream_id, start_ns, now_ns)
        points = ds.get("point", []) if isinstance(ds, dict) else []
        if not points:
            LOGGER.warning("No sleep segment points returned from Google Fit")
            return data

        # 3. Group points into nights, keyed by sleep-night date (shift back 6h so
        #    JST 06:00 is the day boundary, matching fetch_sleep.py).
        nights: dict[datetime.date, dict[str, Any]] = {}

        for p in points:
            try:
                st_ns = int(p["startTimeNanos"])
                en_ns = int(p["endTimeNanos"])
                int_val = p.get("value", [{}])[0].get("intVal")
            except (KeyError, ValueError, TypeError):
                continue

            stage = STAGE_NAMES.get(int_val, "unknown")
            if stage in ("unknown", "out", "sleep"):
                continue

            st_dt = datetime.datetime.fromtimestamp(st_ns / 1e9, tz=datetime.timezone.utc)
            key = (st_dt - datetime.timedelta(hours=6)).date()

            mins = (en_ns - st_ns) / 1e9 / 60.0
            night = nights.setdefault(
                key,
                {"mins": {"light": 0.0, "deep": 0.0, "rem": 0.0, "awake": 0.0},
                 "start": st_ns, "end": en_ns, "device": device, "manufacturer": manufacturer},
            )
            night["mins"][stage] += mins
            night["start"] = min(night["start"], st_ns)
            night["end"] = max(night["end"], en_ns)

        if not nights:
            LOGGER.warning("No staged sleep segments in window (only awake/generic?)")
            return data

        # 4. Pick the most recent COMPLETED night (local date < today)
        today_key = dt_util.now().date()
        candidate_keys = [k for k in nights.keys() if k < today_key]
        if not candidate_keys:
            candidate_keys = list(nights.keys())
        latest_key = max(candidate_keys)
        latest = nights[latest_key]

        light_m = round(latest["mins"]["light"])
        deep_m = round(latest["mins"]["deep"])
        rem_m = round(latest["mins"]["rem"])
        awake_m = round(latest["mins"]["awake"])
        asleep_m = light_m + deep_m + rem_m

        start_local = dt_util.as_local(datetime.datetime.fromtimestamp(
            latest["start"] / 1e9, tz=datetime.timezone.utc))
        end_local = dt_util.as_local(datetime.datetime.fromtimestamp(
            latest["end"] / 1e9, tz=datetime.timezone.utc))

        data.update({
            "sleep_duration": round(asleep_m / 60.0, 1),
            "sleep_deep": round(deep_m / 60.0, 1),
            "sleep_rem": round(rem_m / 60.0, 1),
            "sleep_light": round(light_m / 60.0, 1),
            "sleep_minutes_awake": awake_m,
            "sleep_minutes_asleep": asleep_m,
            "sleep_efficiency": round(asleep_m / (asleep_m + awake_m) * 100.0, 1) if (asleep_m + awake_m) else 0.0,
            "sleep_deep_minutes": deep_m,
            "sleep_rem_minutes": rem_m,
            "sleep_light_minutes": light_m,
            "sleep_device": device,
            "sleep_manufacturer": manufacturer,
            "sleep_start": start_local.isoformat(),
            "sleep_end": end_local.isoformat(),
        })

        # 5. Determine whether currently sleeping
        for p in points:
            try:
                st_ns = int(p["startTimeNanos"])
                en_ns = int(p["endTimeNanos"])
            except (KeyError, ValueError, TypeError):
                continue
            if st_ns <= now_ns < en_ns:
                int_val = p.get("value", [{}])[0].get("intVal")
                stage = STAGE_NAMES.get(int_val, "unknown")
                if stage in ("light", "deep", "rem", "sleep"):
                    data["sleeping"] = True
                    data["sleep_phase"] = stage.upper()
                break

        return data
