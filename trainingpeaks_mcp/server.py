"""
TrainingPeaks MCP Server

Exposes tools for uploading planned workouts and training plans to TrainingPeaks.
Authentication is handled via a headless browser (Playwright) using your normal
TrainingPeaks login credentials — no API partner approval required.

Required environment variables:
  TP_USERNAME   – Your TrainingPeaks username / email
  TP_PASSWORD   – Your TrainingPeaks password

Run:
  python -m trainingpeaks_mcp.server
"""

import os
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from .auth import SessionManager
from .client import TrainingPeaksClient

load_dotenv()

mcp = FastMCP("TrainingPeaks")

# Singletons initialised on first use so env vars are read at call time
_session: Optional[SessionManager] = None
_client: Optional[TrainingPeaksClient] = None
_athlete_id: Optional[str] = None


def _get_client() -> TrainingPeaksClient:
    global _session, _client
    if _client is None:
        _session = SessionManager(
            username=os.environ["TP_USERNAME"],
            password=os.environ["TP_PASSWORD"],
        )
        _client = TrainingPeaksClient(_session)
    return _client


async def _get_athlete_id() -> str:
    global _athlete_id
    if _athlete_id is None:
        athlete = await _get_client().get_athlete()
        _athlete_id = str(athlete["Id"])
    return _athlete_id


# ── Workout type mapping ───────────────────────────────────────────────────────

WORKOUT_TYPES: dict[str, int] = {
    "swim": 1,
    "bike": 2,
    "cycling": 2,
    "road_cycling": 2,
    "gravel": 2,
    "run": 3,
    "trail_run": 3,
    "mountain_bike": 4,
    "mtb": 4,
    "xc_ski": 5,
    "brick": 6,
    "strength": 7,
    "other": 8,
    "rowing": 9,
    "duathlon": 10,
    "triathlon": 11,
    "multisport": 12,
    "walk": 13,
    "hike": 14,
    "yoga": 15,
}


def _resolve_workout_type(workout_type: str) -> int:
    key = workout_type.lower().replace(" ", "_").replace("-", "_")
    if key in WORKOUT_TYPES:
        return WORKOUT_TYPES[key]
    try:
        return int(workout_type)
    except ValueError:
        valid = ", ".join(sorted(WORKOUT_TYPES))
        raise ValueError(f"Unknown workout type {workout_type!r}. Valid types: {valid}")


def _build_api_workout(w: dict) -> dict:
    """Convert user-friendly workout dict to TrainingPeaks API payload."""
    payload: dict = {
        "WorkoutTypeValueId": _resolve_workout_type(w["workout_type"]),
        "Title": w["title"],
        "PlannedDate": w["planned_date"],
    }
    if w.get("duration_minutes") is not None:
        payload["TotalTimePlanned"] = w["duration_minutes"] * 60  # → seconds
    if w.get("distance_km") is not None:
        payload["TotalDistancePlanned"] = w["distance_km"] * 1000  # → meters
    if w.get("description"):
        payload["Description"] = w["description"]
    if w.get("tss") is not None:
        payload["TSSPlanned"] = w["tss"]
    if w.get("intensity_factor") is not None:
        payload["IfPlanned"] = w["intensity_factor"]
    return payload


def _workout_id(obj: dict) -> str:
    return str(obj.get("Id") or obj.get("id") or "unknown")


# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool()
async def upload_workout(
    title: str,
    workout_type: str,
    planned_date: str,
    duration_minutes: Optional[float] = None,
    distance_km: Optional[float] = None,
    description: Optional[str] = None,
    tss: Optional[float] = None,
    intensity_factor: Optional[float] = None,
) -> str:
    """
    Upload a single planned workout to TrainingPeaks.

    Args:
        title: Workout name, e.g. "Easy Run" or "Tempo Intervals"
        workout_type: One of: run, bike, swim, strength, walk, hike, yoga,
                      rowing, triathlon, brick, duathlon, mountain_bike,
                      xc_ski, gravel, multisport, other
        planned_date: Date in YYYY-MM-DD format
        duration_minutes: Planned duration in minutes
        distance_km: Planned distance in kilometres
        description: Workout notes / instructions
        tss: Planned Training Stress Score
        intensity_factor: Planned Intensity Factor (0.0 – 1.0)

    Returns:
        Confirmation message with the created workout ID
    """
    athlete_id = await _get_athlete_id()
    payload = _build_api_workout(
        {
            "title": title,
            "workout_type": workout_type,
            "planned_date": planned_date,
            "duration_minutes": duration_minutes,
            "distance_km": distance_km,
            "description": description,
            "tss": tss,
            "intensity_factor": intensity_factor,
        }
    )
    results = await _get_client().create_workouts(athlete_id, [payload])
    workout_id = _workout_id(results[0])
    return (
        f"Workout created — ID: {workout_id}\n"
        f"Title: {title}\n"
        f"Date:  {planned_date}\n"
        f"Type:  {workout_type}"
    )


@mcp.tool()
async def upload_training_plan(workouts: list[dict]) -> str:
    """
    Upload a full training plan (multiple workouts) to TrainingPeaks in one call.

    Each item in `workouts` is a dict with:
      title          (str, required)  – workout name
      workout_type   (str, required)  – run | bike | swim | strength | …
      planned_date   (str, required)  – YYYY-MM-DD
      duration_minutes (float)        – planned duration in minutes
      distance_km    (float)          – planned distance in kilometres
      description    (str)            – notes / instructions
      tss            (float)          – planned TSS
      intensity_factor (float)        – planned IF (0.0 – 1.0)

    Args:
        workouts: List of workout dicts as described above

    Returns:
        Summary of the uploaded plan with all workout IDs
    """
    if not workouts:
        return "No workouts provided — nothing uploaded."

    athlete_id = await _get_athlete_id()
    payloads = [_build_api_workout(w) for w in workouts]
    results = await _get_client().create_workouts(athlete_id, payloads)

    ids = [_workout_id(r) for r in results]
    date_range = f"{workouts[0]['planned_date']} → {workouts[-1]['planned_date']}"

    lines = [
        f"Training plan uploaded — {len(ids)} workout(s) created.",
        f"Date range: {date_range}",
        "",
    ]
    for w, wid in zip(workouts, ids):
        lines.append(f"  [{wid}]  {w['planned_date']}  {w['workout_type']:12s}  {w['title']}")

    return "\n".join(lines)


@mcp.tool()
async def get_athlete_info() -> str:
    """
    Return the TrainingPeaks athlete profile linked to the configured credentials.

    Returns:
        Athlete ID, name, email, and username
    """
    athlete = await _get_client().get_athlete()
    return (
        f"Athlete ID: {athlete.get('Id')}\n"
        f"Name:       {athlete.get('FirstName')} {athlete.get('LastName')}\n"
        f"Email:      {athlete.get('Email')}\n"
        f"Username:   {athlete.get('Username')}"
    )


@mcp.tool()
def list_workout_types() -> str:
    """
    List every workout type accepted by the upload tools.

    Returns:
        Formatted list of type keys and their meanings
    """
    descriptions: dict[str, str] = {
        "run": "Running (road or track)",
        "trail_run": "Trail running (maps to run)",
        "bike": "Road cycling",
        "gravel": "Gravel / adventure cycling (maps to bike)",
        "mountain_bike": "Mountain biking",
        "swim": "Swimming",
        "strength": "Strength / gym training",
        "walk": "Walking",
        "hike": "Hiking",
        "yoga": "Yoga / flexibility",
        "rowing": "Rowing / ergometer",
        "brick": "Brick (multi-discipline combo)",
        "duathlon": "Duathlon",
        "triathlon": "Triathlon",
        "multisport": "Multisport",
        "xc_ski": "Cross-country skiing",
        "other": "Other / uncategorised",
    }
    lines = ["Supported workout types:\n"]
    for key, desc in descriptions.items():
        lines.append(f"  {key:<16} {desc}")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
