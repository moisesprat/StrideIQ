from enum import Enum
from pydantic import BaseModel


class AspectType(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class ObjectType(str, Enum):
    ACTIVITY = "activity"
    ATHLETE = "athlete"


class StravaWebhookEvent(BaseModel):
    """
    Payload Strava sends to the webhook endpoint when an event occurs.
    Docs: https://developers.strava.com/docs/webhooks/
    """

    aspect_type: AspectType
    event_time: int
    object_id: int          # activity_id or athlete_id
    object_type: ObjectType
    owner_id: int           # athlete_id of the resource owner
    subscription_id: int
    updates: dict = {}
