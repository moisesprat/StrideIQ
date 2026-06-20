import httpx

from .auth import TokenManager

TP_API_BASE = "https://tpapi.trainingpeaks.com"


class TrainingPeaksClient:
    def __init__(self, token_manager: TokenManager):
        self._tokens = token_manager

    async def _headers(self) -> dict[str, str]:
        token = await self._tokens.get_access_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def get_athlete(self) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{TP_API_BASE}/fitness/v1/athletes/me",
                headers=await self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def create_workouts(self, athlete_id: str, workouts: list[dict]) -> list[dict]:
        """POST an array of planned workouts; returns the array of created objects."""
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{TP_API_BASE}/fitness/v1/athletes/{athlete_id}/workouts",
                json=workouts,
                headers=await self._headers(),
                timeout=30.0,
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else [data]
