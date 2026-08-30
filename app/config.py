"""Application configuration, loaded from environment variables."""
import os
from pathlib import Path


class Settings:
    li_at: str | None = os.getenv("LINKEDIN_LI_AT") or None
    email: str | None = os.getenv("LINKEDIN_EMAIL") or None
    password: str | None = os.getenv("LINKEDIN_PASSWORD") or None
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
    batch_max_size: int = int(os.getenv("BATCH_MAX_SIZE", "20"))
    batch_delay_seconds: float = float(os.getenv("BATCH_DELAY_SECONDS", "1.5"))
    session_cache_path: Path = Path(os.getenv("SESSION_CACHE_PATH", "./data/session_cache.json"))
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))


settings = Settings()
