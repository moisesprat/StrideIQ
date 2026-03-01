# StrideIQ — Ingestion Service

Receives Strava workout events via webhook and stores raw activity data in Cloudflare R2.

## How it works

```
New Strava workout
       │
       ▼
Strava Webhook POST /webhook
       │
       ▼
Fetch full activity + streams from Strava API  (parallel)
       │
       ▼
Store as JSON in Cloudflare R2
  activities/{athlete_id}/{year}/{month}/{activity_id}.json
```

The stored payload contains:
- Full activity metadata (distance, duration, pace, splits, effort zones, …)
- Time-series streams: GPS track, heart rate, cadence, power, altitude, speed

## Setup

### 1. Create a Strava API application

1. Go to https://www.strava.com/settings/api
2. Create an app (the callback domain can be `localhost` for local dev)
3. Note your **Client ID** and **Client Secret**

### 2. Configure environment

```bash
cd ingestion
cp .env.example .env
# Fill in STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, and R2 credentials
```

Get R2 API credentials from:
- Cloudflare Dashboard → R2 → Manage R2 API Tokens
- Required permissions: **Object Read & Write** on the `strideiq-raw` bucket

### 3. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Start the service

```bash
uvicorn app.main:app --reload
```

### 5. Connect your Strava account

The service needs a **refresh token** to call the Strava API on your behalf.

**Option A — OAuth flow (recommended):**

1. Expose the service publicly (e.g. `ngrok http 8000`)
2. Set `APP_BASE_URL=https://xxxx.ngrok.io` in `.env`
3. Visit `http://localhost:8000/auth/strava` in your browser
4. Approve the Strava permission screen
5. Copy the `refresh_token` from the JSON response
6. Add it to `.env` as `STRAVA_REFRESH_TOKEN=<token>`

**Option B — Pre-configured token:**
If you already have a refresh token with `activity:read_all` scope, set it directly in `.env`.

### 6. Register the webhook subscription

The service must be publicly reachable before running this.

```bash
# From inside the ingestion/ directory
python scripts/register_webhook.py

# List existing subscriptions
python scripts/register_webhook.py --list

# Delete a subscription
python scripts/register_webhook.py --delete <id>
```

Strava allows only **one** webhook subscription per app.

## Docker

```bash
# Build
docker build -t strideiq-ingestion .

# Run (pass env vars or mount a .env file)
docker run -p 8000:8000 --env-file .env strideiq-ingestion
```

## R2 Data Layout

```
strideiq-raw/
└── activities/
    └── {athlete_id}/
        └── {year}/
            └── {month}/
                └── {activity_id}.json
```

Each file contains:

```json
{
  "schema_version": "1.0",
  "ingested_at": "2026-03-01T10:00:00+00:00",
  "athlete_id": 12345,
  "activity_id": 99999,
  "activity": { /* full Strava activity object */ },
  "streams": {
    "time": { "data": [...] },
    "latlng": { "data": [...] },
    "heartrate": { "data": [...] },
    "watts": { "data": [...] }
  }
}
```

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/auth/strava` | Start Strava OAuth flow |
| `GET` | `/auth/strava/callback` | OAuth callback |
| `GET` | `/webhook` | Strava subscription verification |
| `POST` | `/webhook` | Receive Strava activity events |

Interactive docs: http://localhost:8000/docs
