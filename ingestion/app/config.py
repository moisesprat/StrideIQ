from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Strava OAuth ──────────────────────────────────────────────────────────
    strava_client_id: str
    strava_client_secret: str
    strava_verify_token: str = "strideiq_verify"
    # Populated after the first OAuth flow; can be pre-set via env var
    strava_refresh_token: str = ""

    # ── Cloudflare R2 ─────────────────────────────────────────────────────────
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket_name: str = "strideiq-raw"

    # ── App ───────────────────────────────────────────────────────────────────
    app_base_url: str = "http://localhost:8000"

    @property
    def r2_endpoint_url(self) -> str:
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
