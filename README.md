# Google Fit Sleep Integration for Home Assistant

A custom Home Assistant integration that reads **sleep data (light / deep / REM / awake)** from Google's health APIs and exposes it as sensors.

> ⚠️ This is NOT the official HA integration. Data source is dual:
> 1. **Google Health API v4** (`health.googleapis.com`) — new account-centric API, preferred when data is available (Health Connect → Google Health sync)
> 2. **Google Fit API v1** (`fitness.googleapis.com`) — legacy API used as automatic fallback (supported until end of 2026)

---

## Why this exists

The stock HA Google Health integration only exposes *total* sleep duration. Sleep **stages** (deep / REM / light breakdown) are not available through:

* HA Companion App → Health Connect sensors (totals only, no stages)
* The official Google Health (v4) integration's stock sensors (no stages)

Sleep stage data lives in Google's cloud (via Health Connect sync to **Google Health** and/or **Google Fit**). This integration reads whichever source has data — v4 first, Fit as fallback — and aggregates the most recent completed night.

---

## Features

* **Sleep stage breakdown** for the latest night: light / deep / REM / awake minutes, plus sleep efficiency
* **Hours sensors**: Last Sleep Duration, Last Deep Sleep Duration, Last REM Sleep Duration, Last Light Sleep Duration
* **Sleeping binary sensor**: `on` while a sleep-stage segment is currently active (with `phase` attribute)
* **Device attribution**: source device model / manufacturer from the Google Fit data source

### Exposed sensors (device: `<name> Google Health Account`)

* `sensor.<name>_google_health_account_last_sleep_duration` (attributes: minutes_asleep / awake, light / deep / rem minutes, efficiency %, start / end time, source device)
* `sensor.<name>_google_health_account_last_deep_sleep_duration`
* `sensor.<name>_google_health_account_last_rem_sleep_duration`
* `sensor.<name>_google_health_account_last_light_sleep_duration`
* `binary_sensor.<name>_google_health_account_sleeping`

---

## Installation via HACS

1. Open your Home Assistant dashboard, go to **HACS** > **Integrations**.
2. Click the **three dots** (top-right) and select **Custom repositories**.
3. Paste: `https://github.com/manate-anagram/ha-google-fit-sleep`
4. Select **Integration** as the Category and click **Add**.
5. Click **Install** on the Google Health (Fit) card.
6. **Restart Home Assistant** (Settings > System > Restart).

---

## Google Cloud Console OAuth Setup

Requires an OAuth client with **Google Fit API** enabled.

### 1. Enable the API
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or reuse one).
3. **APIs & Services > Library** → enable **Google Fit API** (and **Google Health API** if available).
4. **APIs & Services > OAuth consent screen**:
   * User Type: **External**
   * Add your Google account to the **test users** list (required while app is in Testing mode).
   * Add scopes: `.../auth/fitness.sleep.read` and `.../auth/googlehealth.sleep.read`

### 2. Create OAuth credentials
1. **APIs & Services > Credentials > Create Credentials > OAuth client ID**.
2. Application type: **Web application**.
3. **Authorized redirect URIs**: `https://my.home-assistant.io/redirect/oauth`
4. Copy the **Client ID** and **Client Secret**.

---

## Configuration in Home Assistant

1. **Settings > Devices & Services > Add Integration** → search **Google Health** (the domain is `google_health` for dashboard compatibility; data source is Google Health v4 / Google Fit).
2. Enter your Client ID / Client Secret when prompted.
3. Authorize with your Google account (consent screen; scopes: `fitness.sleep.read` + `googlehealth.sleep.read`).
4. Enter a display name (default: `My`).

---

## Troubleshooting

* **Entities stay 0** → check the HA log for `Google Fit API` warnings. Common causes:
  * The Google account has no staged sleep data in Google Fit (e.g. band not syncing, or only "sleep" without stage classification).
  * The OAuth client lacks `fitness.sleep.read` scope → re-authenticate the integration.
* **"No com.google.sleep.segment data source found"** → the connected Google account has never synced staged sleep segments (Xiaomi / Mi Fitness data usually appears as `raw:...:com.xiaomi.hm.health`).

---

## Credits

* Forked from [actstorms/ha-google-health](https://github.com/actstorms/ha-google-health) (Google Health v4 API client) and repurposed to read the Google Fit API v1 sleep segments.
* Aggregation logic adapted from the `google-fit-sleep` skill's `fetch_sleep.py`.
