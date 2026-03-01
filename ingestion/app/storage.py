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

from app.config import get_settings

logger = logging.getLogger(__name__)


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


async def store_activity(
    athlete_id: int,
    activity_id: int,
    activity_data: dict,
    streams_data: dict,
) -> str:
    """
    Persist raw activity + streams data to R2.

    Returns the R2 object key where the data was stored.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)

    key = f"activities/{athlete_id}/{now.year}/{now.month:02d}/{activity_id}.json"

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
