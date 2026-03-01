"""
Cloudflare R2 storage client.

Stores raw activity payloads (activity metadata + streams) as JSON files
under the following path structure:

    activities/{athlete_id}/{year}/{month:02d}/{activity_id}.json
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import get_settings

logger = logging.getLogger(__name__)


def _activity_key(athlete_id: int, activity_id: int, start_date: str) -> str:
    """
    Derive the R2 object key for an activity.

    Uses the activity's own start_date so files are organised by when the
    workout happened, not when it was ingested (important for backfills).
    Falls back to the current UTC time when start_date is absent/invalid.
    """
    try:
        dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        dt = datetime.now(timezone.utc)
    return f"activities/{athlete_id}/{dt.year}/{dt.month:02d}/{activity_id}.json"


def _sync_exists(key: str, bucket: str) -> bool:
    """Return True if the R2 object already exists (synchronous, runs in thread)."""
    client = _make_r2_client()
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        raise


def _make_r2_client():
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "adaptive"},
        ),
    )


def _sync_upload(key: str, body: bytes, metadata: dict[str, str], bucket: str) -> None:
    """Synchronous upload — runs inside a thread pool via asyncio.to_thread."""
    client = _make_r2_client()
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
        Metadata=metadata,
    )
    logger.info("Stored → R2://%s/%s", bucket, key)


async def activity_exists(athlete_id: int, activity_id: int, start_date: str) -> bool:
    """Return True if this activity has already been stored in R2."""
    settings = get_settings()
    key = _activity_key(athlete_id, activity_id, start_date)
    return await asyncio.to_thread(_sync_exists, key, settings.r2_bucket_name)


async def store_activity(
    athlete_id: int,
    activity_id: int,
    activity_data: dict,
    streams_data: dict,
) -> str:
    """
    Persist raw activity + streams data to R2.

    The R2 key is derived from the activity's own start_date so the folder
    structure reflects when the workout happened, not when it was ingested.

    Returns the R2 object key where the data was stored.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)

    key = _activity_key(athlete_id, activity_id, activity_data.get("start_date", ""))

    payload = {
        "schema_version": "1.0",
        "ingested_at": now.isoformat(),
        "athlete_id": athlete_id,
        "activity_id": activity_id,
        "activity": activity_data,
        "streams": streams_data,
    }

    metadata = {
        "athlete-id": str(athlete_id),
        "activity-id": str(activity_id),
        "ingested-at": now.isoformat(),
        "activity-type": activity_data.get("type", "unknown"),
        "activity-name": activity_data.get("name", ""),
    }

    body = json.dumps(payload, indent=2).encode("utf-8")

    await asyncio.to_thread(_sync_upload, key, body, metadata, settings.r2_bucket_name)
    return key
