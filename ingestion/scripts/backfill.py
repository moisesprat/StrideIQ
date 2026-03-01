#!/usr/bin/env python3
"""
Historical backfill — fetch all Strava activities into R2.

Run from the ingestion/ directory so that .env is picked up automatically:

    cd ingestion
    python scripts/backfill.py

Options:
    --after  YYYY-MM-DD   Only import activities after this date (inclusive)
    --before YYYY-MM-DD   Only import activities before this date (inclusive)
    --delay  SECONDS      Sleep between each activity (default: 2 s)
    --skip-existing       Skip activities already stored in R2
    --dry-run             List activities without writing anything to R2

Strava rate limits: 100 requests / 15 min, 1 000 / day.
Each activity costs 2 requests (details + streams), so the default 2-second
delay keeps throughput well under the per-15-minute cap.
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the app package importable when running as a standalone script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.storage import activity_exists, store_activity  # noqa: E402
from app.strava import (  # noqa: E402
    get_activity,
    get_activity_streams,
    list_activities,
    token_manager,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger("backfill")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_date_to_ts(date_str: str) -> int:
    """Convert a YYYY-MM-DD string to a UTC Unix timestamp."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


# ── Core logic ────────────────────────────────────────────────────────────────


async def _ingest_one(athlete_id: int, activity_id: int, dry_run: bool) -> bool:
    """Fetch full details + streams for one activity and store in R2.

    Returns True on success, False on error.
    """
    try:
        activity_data, streams_data = await asyncio.gather(
            get_activity(activity_id),
            get_activity_streams(activity_id),
            return_exceptions=True,
        )

        if isinstance(activity_data, BaseException):
            logger.error("  Failed to fetch activity %s: %s", activity_id, activity_data)
            return False

        if isinstance(streams_data, BaseException):
            logger.warning("  No streams for activity %s: %s", activity_id, streams_data)
            streams_data = {}

        if dry_run:
            logger.info(
                "  [dry-run] Would store '%s' (%s)",
                activity_data.get("name"),
                activity_data.get("sport_type", activity_data.get("type")),
            )
        else:
            r2_key = await store_activity(athlete_id, activity_id, activity_data, streams_data)
            logger.info("  Stored → R2: %s", r2_key)

        return True

    except Exception:
        logger.exception("  Unexpected error ingesting activity %s", activity_id)
        return False


async def run_backfill(
    after: int | None,
    before: int | None,
    delay: float,
    skip_existing: bool,
    dry_run: bool,
) -> None:
    settings = get_settings()

    if not settings.strava_refresh_token:
        logger.error(
            "STRAVA_REFRESH_TOKEN is not set. "
            "Complete the OAuth flow at /auth/strava first, then add the token to .env."
        )
        sys.exit(1)

    # Seed the in-memory token manager from the env-configured refresh token.
    token_manager.set_refresh_token(settings.strava_refresh_token)

    ingested = skipped = failed = 0
    page = 1
    per_page = 50

    logger.info(
        "Backfill starting | after=%s before=%s skip_existing=%s dry_run=%s",
        after,
        before,
        skip_existing,
        dry_run,
    )

    while True:
        logger.info("── Page %d ─────────────────────────────", page)
        try:
            summaries = await list_activities(
                before=before, after=after, page=page, per_page=per_page
            )
        except Exception:
            logger.exception("Failed to list activities on page %d", page)
            break

        if not summaries:
            logger.info("No more activities — backfill complete.")
            break

        for summary in summaries:
            activity_id: int = summary["id"]
            athlete_id: int = summary.get("athlete", {}).get("id", 0)
            start_date: str = summary.get("start_date", "")
            name: str = summary.get("name", "")
            sport: str = summary.get("sport_type", summary.get("type", "?"))
            date_label = start_date[:10] if start_date else "?"

            logger.info("Activity %s — %s [%s] %s", activity_id, name, sport, date_label)

            if skip_existing and athlete_id and start_date:
                try:
                    if await activity_exists(athlete_id, activity_id, start_date):
                        logger.info("  Already stored, skipping.")
                        skipped += 1
                        continue
                except Exception:
                    logger.warning("  Could not check existence; will re-ingest.")

            success = await _ingest_one(athlete_id, activity_id, dry_run)
            if success:
                ingested += 1
            else:
                failed += 1

            if delay > 0:
                await asyncio.sleep(delay)

        page += 1
        # Small courtesy pause between listing pages.
        await asyncio.sleep(1)

    logger.info(
        "Done | ingested=%d  skipped=%d  failed=%d",
        ingested,
        skipped,
        failed,
    )


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill historical Strava activities into Cloudflare R2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--after",
        metavar="YYYY-MM-DD",
        help="Only import activities on or after this date.",
    )
    parser.add_argument(
        "--before",
        metavar="YYYY-MM-DD",
        help="Only import activities on or before this date.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="Seconds to sleep between each activity fetch (default: 2).",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip activities that are already stored in R2.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List and log activities without writing anything to R2.",
    )
    args = parser.parse_args()

    after_ts = _parse_date_to_ts(args.after) if args.after else None
    before_ts = _parse_date_to_ts(args.before) if args.before else None

    asyncio.run(
        run_backfill(
            after=after_ts,
            before=before_ts,
            delay=args.delay,
            skip_existing=args.skip_existing,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
