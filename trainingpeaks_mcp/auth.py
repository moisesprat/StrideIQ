import time

import httpx

TP_TOKEN_URL = "https://oauth.trainingpeaks.com/oauth/token"
TP_AUTH_URL = "https://oauth.trainingpeaks.com/OAuth/Authorize"


class TokenManager:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._access_token: str = ""
        self._expires_at: float = 0.0

    async def get_access_token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        await self._refresh()
        return self._access_token

    async def _refresh(self) -> None:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                TP_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "scope": "workouts:write athlete:profile",
                },
            )
            r.raise_for_status()
            data = r.json()

        self._access_token = data["access_token"]
        self._expires_at = time.time() + data.get("expires_in", 3600)
        # TrainingPeaks may rotate the refresh token
        if "refresh_token" in data:
            self.refresh_token = data["refresh_token"]
