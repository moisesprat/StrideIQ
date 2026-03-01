"""
StrideIQ — Ingestion Service

Endpoints:
  GET  /health                   Health check
  GET  /auth/strava              Start Strava OAuth flow
  GET  /auth/strava/callback     OAuth callback (exchange code for tokens)
  GET  /webhook                  Strava webhook subscription verification
  POST /webhook                  Receive Strava webhook events
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import get_settings
from app.models import AspectType, ObjectType, StravaWebhookEvent
from app.strava import (
    STRAVA_OAUTH_URL,
    exchange_code_for_tokens,
    get_activity,
    get_activity_streams,
    token_manager,
)
from app.storage import store_activity

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("StrideIQ Ingestion Service starting up")
    yield
    logger.info("StrideIQ Ingestion Service shutting down")


app = FastAPI(
    title="StrideIQ — Ingestion Service",
    description="Strava webhook receiver and raw data ingestion to Cloudflare R2",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "service": "strideiq-ingestion", "version": "0.1.0"}


# ── Strava OAuth ──────────────────────────────────────────────────────────────


@app.get("/auth/strava", tags=["auth"])
async def strava_auth():
    """
    Redirect the browser to Strava's OAuth authorization page.

    After approving, Strava redirects back to /auth/strava/callback.
    """
    settings = get_settings()
    params = (
        f"client_id={settings.strava_client_id}"
        f"&redirect_uri={settings.app_base_url}/auth/strava/callback"
        f"&response_type=code"
        f"&approval_prompt=auto"
        f"&scope=read,activity:read_all"
    )
    return RedirectResponse(url=f"{STRAVA_OAUTH_URL}?{params}")


@app.get("/auth/strava/callback", tags=["auth"])
async def strava_callback(
    code: str = Query(...),
    scope: str = Query(default=""),
    error: str = Query(default=""),
):
    """
    Handle Strava's OAuth redirect.

    On success, returns the refresh token — copy it into your .env as
    STRAVA_REFRESH_TOKEN so the service can fetch activities automatically.
    """
    if error:
        raise HTTPException(status_code=400, detail=f"Strava OAuth error: {error}")

    try:
        token_data = await exchange_code_for_tokens(code)
    except Exception as exc:
        logger.exception("Token exchange failed")
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}")

    refresh_token: str = token_data["refresh_token"]
    athlete: dict = token_data.get("athlete", {})

    # Store in-memory so the service can immediately use it
    token_manager.set_refresh_token(refresh_token)

    logger.info(
        "OAuth complete — athlete %s (%s %s)",
        athlete.get("id"),
        athlete.get("firstname"),
        athlete.get("lastname"),
    )

    return {
        "status": "connected",
        "athlete": {
            "id": athlete.get("id"),
            "name": f"{athlete.get('firstname')} {athlete.get('lastname')}",
        },
        "refresh_token": refresh_token,
        "next_step": "Add STRAVA_REFRESH_TOKEN to your .env to persist across restarts",
    }


# ── Strava Webhook ────────────────────────────────────────────────────────────


@app.get("/webhook", tags=["webhook"])
async def webhook_verify(
    hub_mode: str = Query(alias="hub.mode"),
    hub_challenge: str = Query(alias="hub.challenge"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
):
    """
    Strava webhook subscription verification.

    Strava sends this GET request when you register a webhook subscription.
    We must echo back hub.challenge if the verify token matches.
    """
    settings = get_settings()

    if hub_mode != "subscribe":
        raise HTTPException(status_code=400, detail="Unexpected hub.mode")

    if hub_verify_token != settings.strava_verify_token:
        logger.warning("Webhook verification failed — bad verify token")
        raise HTTPException(status_code=403, detail="Invalid verify token")

    logger.info("Webhook subscription verified successfully")
    return JSONResponse(content={"hub.challenge": hub_challenge})


@app.post("/webhook", tags=["webhook"])
async def webhook_receive(request: Request, background_tasks: BackgroundTasks):
    """
    Receive Strava activity events.

    Strava expects a 200 response within 2 seconds — heavy work runs in the
    background so we can return immediately.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    logger.info("Webhook event: %s", json.dumps(body))

    try:
        event = StravaWebhookEvent(**body)
    except Exception as exc:
        logger.warning("Unparseable webhook event (%s): %s", exc, body)
        # Return 200 to prevent Strava from endlessly retrying
        return {"status": "ignored", "reason": "unparseable"}

    if event.object_type == ObjectType.ACTIVITY and event.aspect_type == AspectType.CREATE:
        background_tasks.add_task(
            _ingest_activity, athlete_id=event.owner_id, activity_id=event.object_id
        )
        return {"status": "queued", "activity_id": event.object_id}

    logger.info(
        "Skipping event — type=%s aspect=%s", event.object_type, event.aspect_type
    )
    return {"status": "ignored", "reason": "not an activity create event"}


async def _ingest_activity(athlete_id: int, activity_id: int) -> None:
    """
    Background task: fetch activity + streams from Strava, store in R2.
    """
    logger.info("Ingesting activity %s for athlete %s", activity_id, athlete_id)

    try:
        # Fetch metadata and time-series streams in parallel
        activity_data, streams_data = await asyncio.gather(
            get_activity(activity_id),
            get_activity_streams(activity_id),
            return_exceptions=True,
        )

        if isinstance(activity_data, BaseException):
            raise activity_data  # re-raise so it's caught below

        if isinstance(streams_data, BaseException):
            logger.warning(
                "Could not fetch streams for activity %s: %s", activity_id, streams_data
            )
            streams_data = {}

        r2_key = await store_activity(athlete_id, activity_id, activity_data, streams_data)
        logger.info(
            "Activity %s ingested successfully → R2 key: %s", activity_id, r2_key
        )

    except Exception:
        logger.exception(
            "Failed to ingest activity %s for athlete %s", activity_id, athlete_id
        )
