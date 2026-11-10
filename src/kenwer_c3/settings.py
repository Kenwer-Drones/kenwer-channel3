"""Application settings loaded from environment / .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "kenwer-c3"
    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = Field(
        default="postgresql+psycopg://kenwer:kenwer@localhost:5433/kenwer_c3"
    )

    redis_url: str = "redis://localhost:6380/0"
    celery_broker_url: str = "redis://localhost:6380/0"
    celery_result_backend: str = "redis://localhost:6380/1"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "kenwerminio"
    s3_secret_key: str = "kenwerminio"
    s3_bucket: str = "kenwer-c3-evidence"
    s3_region: str = "us-east-1"

    # Comma-separated "key:role" pairs for local development only
    dev_api_keys: str = (
        "dev-admin-key:admin,"
        "dev-ingest-key:channel2_ingest,"
        "dev-retrieve-key:channel1_retrieve,"
        "dev-reviewer-key:reviewer"
    )
    dev_tenant_id: str = "00000000-0000-4000-8000-000000000001"
    dev_cognitive_identity_id: str = "00000000-0000-4000-8000-000000000010"

    llm_provider: str = "mock"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 2
    # When false (default), production provider must not be used with real mission content
    # without an operator explicitly enabling external LLM transmission.
    llm_allow_external_mission_content: bool = False

    otel_enabled: bool = False
    beacon_enabled: bool = False
    beacon_otlp_endpoint: str | None = None

    # When true, Celery runs tasks inline (unit/API tests). Real-worker tests set false.
    celery_task_always_eager: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
