"""The Google Health (Google Fit) integration.

Data source: Google Fit API v1 (fitness.googleapis.com).
This fork reads sleep segments (com.google.sleep.segment) from dataSources
and aggregates per-night totals (light / deep / REM / awake) for the last
completed night, exposing them as the same sensor keys as the original
actstorms integration so dashboards keep working.
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
    """API Client for interacting with the Google Fit API (v1)."""

    def __init__(self, hass: HomeAssistant, oauth_session: config_entry_oauth2_flow.OAuth2Session) -> None:
        """Initialize the API client."""
        self.hass = hass
        self.oauth_session = oauth_session
        self.client_session = aiohttp_client.async_get_clientsession(hass)

    async def _request(self, method: str, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make an authenticated HTTP request to the Google Fit API with a 10s timeout."""
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
                    "Google Fit API error: %s %s -> HTTP %s body=%s",
                    method, url, response.status, body_text[:1000],
                )
            response.raise_for_status()
            return json.loads(body_text) if body_text else {}

    async def _safe_request(self, method: str, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Safely make request, logging warnings on failure."""
        try:
            return await self._request(method, url, params)
        except Exception as err:
            LOGGER.warning("Failed to fetch Google Fit endpoint '%s': %s", url, err)
            return {}

    async def get_data_sources(self) -> dict[str, Any]:
        """Retrieve all data sources."""
        return await self._safe_request("GET", f"{FITNESS_API_BASE}/dataSources")

    async def get_dataset(self, data_stream_id: str, start_nanos: int, end_nanos: int) -> dict[str, Any]:
        """Retrieve a dataset (points) for a data source between nanosecond timestamps."""
        encoded = urllib.parse.quote(data_stream_id, safe="")
        url = f"{FITNESS_API_BASE}/dataSources/{encoded}/datasets/{start_nanos}-{end_nanos}"
        return await self._safe_request("GET", url)


class GoogleHealthDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage polling Google Fit sleep data."""

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
        """Fetch sleep segment data from Google Fit and aggregate the latest night."""
        try:
            return await self._fetch_sleep_summary()
        except Exception as err:
            raise UpdateFailed(f"Failed to fetch data from Google Fit API: {err}") from err

    async def _fetch_sleep_summary(self) -> dict[str, Any]:
        """Aggregate sleep segments into the data dict used by sensors."""
        # Empty default payload (all keys preserved for sensor attributes)
        data: dict[str, Any] = {
            "sleep_duration": 0.0,
            "sleep_deep": 0.0,
            "sleep_rem": 0.0,
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

        # 3. Group points into nights by civil date (JST): night = date at 12:00 JST of the
        #    calendar day in which the segment starts, with 12:00 cutoff so post-midnight
        #    segments of one sleep session don't split weirdly. Simpler: use JST date.
        #    We only report the MOST RECENT night.
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
                # skip unclassified/out-of-bed; "sleep" (2) = generic sleep w/o stage
                continue

            st_dt = datetime.datetime.fromtimestamp(st_ns / 1e9, tz=datetime.timezone.utc)
            # Night bucket keyed by "sleep night" date: shift back 6h so JST 06:00 is the
            # day boundary (matches fetch_sleep.py: post-midnight segments belong to
            # the previous calendar night).
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

        # 4. Pick the most recent COMPLETED night (local date < today). A sleep that
        #    started last night and ended this morning may still have segments keyed
        #    to "today" if it extends past 06:00 JST; exclude today's partial bucket.
        today_key = dt_util.now().date()
        candidate_keys = [k for k in nights.keys() if k < today_key]
        if not candidate_keys:
            # Fall back to any night bucket (e.g. currently sleeping, or data is fresh)
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
            "sleeping": False,
            "sleep_phase": "AWAKE",
            "sleep_minutes_awake": awake_m,
            "sleep_minutes_asleep": asleep_m,
            "sleep_time_to_fall_asleep": 0,
            "sleep_efficiency": round(asleep_m / (asleep_m + awake_m) * 100.0, 1) if (asleep_m + awake_m) else 0.0,
            "sleep_deep_minutes": deep_m,
            "sleep_rem_minutes": rem_m,
            "sleep_light_minutes": light_m,
            "sleep_device": device,
            "sleep_manufacturer": manufacturer,
            "sleep_start": start_local.isoformat(),
            "sleep_end": end_local.isoformat(),
        })

        # 5. Determine whether currently sleeping (any staged segment overlapping now)
        now_local = dt_util.now()
        if latest["start"] <= now_ns <= latest["end"]:
            # crude overlap: latest recorded night spans now
            pass
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
