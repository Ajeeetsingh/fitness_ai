import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "Heybobo Fitness"
    API_V1_STR: str = "/api"
    LLM_BASE_HOST: str = "https://h200.yatycloud.com"
    LLM_BASE_URL: str = "https://h200.yatycloud.com/large/generate"
    LLM_TIMEOUT: float = 120.0  # Fixed: Changed from string "120" to float 120.0
    LLM_SYSTEM: str = "You are a safety-conscious fitness coach."
    BASE_DIR: str = os.path.dirname(os.path.abspath('.'))
    STORAGE_DIR: str = os.path.join(BASE_DIR, "storage")
    ATHLETE_DIR: str = os.path.join(STORAGE_DIR, "athlete")
    LOG_CSV_PATH: str = os.getenv("PLAN_LOG_CSV", os.path.join(STORAGE_DIR, "plan_runs.csv"))

    # Pose analyzer storage (for processed videos, summaries, etc.)
    POSE_STORAGE_DIR: str = os.getenv("POSE_STORAGE_DIR", os.path.join(STORAGE_DIR, "pose"))
    POSE_TMP_DIR: str = os.getenv("POSE_TMP_DIR", os.path.join(BASE_DIR, "tmp", "pose_uploads"))

    # Environment / debug flags
    # IS_DEVELOPMENT should normally be false in production; can be toggled via env for extra logging/overlays.
    IS_DEVELOPMENT: bool = os.getenv("IS_DEVELOPMENT", "false").lower() == "true"
    # When true, also log per-frame events in LIVE mode; by default we only log events for recorded analysis.
    POSE_LOG_LIVE_EVENTS: bool = os.getenv("POSE_LOG_LIVE_EVENTS", "false").lower() == "true"

    # YouTube Links Configuration
    YOUTUBE_API_KEY: str = os.getenv("YOUTUBE_API_KEY", "")
    YOUTUBE_CACHE_PATH: str = os.getenv("YOUTUBE_CACHE_PATH", os.path.join(STORAGE_DIR, "youtube_links_cache.json"))
    YOUTUBE_CACHE_TTL_DAYS: int = int(os.getenv("YOUTUBE_CACHE_TTL_DAYS", "30"))

    # os.makedirs(STORAGE_DIR, exist_ok=True)
    # os.makedirs(ATHLETE_DIR, exist_ok=True)

    class Config:
        env_file = ".env"


settings = Settings()
