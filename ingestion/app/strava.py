"""
Strava API client.

Handles:
- OAuth authorization code exchange
- Access token refresh (with in-memory caching)
- Fetching activity details and streams
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_OAUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_API_BASE = "https://www.strava.com/api/v3"

STREAM_KEYS = "time,latlng,distance,altitude,heartrate,cadence,watts,velocity_smooth,moving"


class TokenManager:
    """Thread-safe manager for Strava access tokens with auto-refresh."""

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._expires_at: datetime | None = None
        self._refresh_token: str | None = None
        self._lock = asyncio.Lock()

    def set_refresh_token(self, refresh_token: str) -> None:
        """Store a new refresh token (e.g. after OAuth callback)."""
        self._refresh_token = refresh_token
        # Invalidate cached access token so the next call forces a refresh
        self._access_token = None
        self._expires_at = None

    async def get_access_token(self) -> str:
        async with self._lock:
            settings = get_settings()
            refresh_token = self._refresh_token or settings.strava_refresh_token

            if not refresh_token:
                raise RuntimeError(
                    "No Strava refresh token configured. "
                    "Complete the OAuth flow at /auth/strava first, "
                    "then set STRAVA_REFRESH_TOKEN in your .env."
                )

            # Return cached token if still valid (with 5-minute safety buffer)
            now = datetime.now(timezone.utc)
            if (
                self._access_token
                and self._expires_at
                and now < self._expires_at - timedelta(minutes=5)
            ):
                return self._access_token

            # Refresh
            logger.info("Refreshing Strava access token...")
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    STRAVA_TOKEN_URL,
                    data={
                        "client_id": settings.strava_client_id,
                        "client_secret": settings.strava_client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
                resp.raise_for_status()
                token_data = resp.json()

            self._access_token = token_data["access_token"]
            self._refresh_token = token_data["refresh_token"]
            self._expires_at = datetime.fromtimestamp(
                token_data["expires_at"], tz=timezone.utc
            )
            logger.info("Access token refreshed, expires at %s", self._expires_at)
            return self._access_token  # type: ignore[return-value]


# Singleton — shared across requests
token_manager = TokenManager()


async def exchange_code_for_tokens(code: str) -> dict:
    """Exchange an OAuth authorization code for access + refresh tokens."""
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            STRAVA_TOKEN_URL,
            data={
                "client_id": settings.strava_client_id,
                "client_secret": settings.strava_client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def get_activity(activity_id: int) -> dict:
    """Fetch full activity details from the Strava API."""
    access_token = await token_manager.get_access_token()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{STRAVA_API_BASE}/activities/{activity_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"include_all_efforts": True},
        )
        resp.raise_for_status()
        return resp.json()


async def get_activity_streams(activity_id: int) -> dict:
    """
    Fetch time-series streams (GPS, heart rate, power, etc.) for an activity.
    Returns an empty dict if no streams are available.
    """
    access_token = await token_manager.get_access_token()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{STRAVA_API_BASE}/activities/{activity_id}/streams",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"keys": STREAM_KEYS, "key_by_type": True},
        )
        if resp.status_code == 404:
            logger.info("No streams available for activity %s", activity_id)
            return {}
        resp.raise_for_status()
        return resp.json()
