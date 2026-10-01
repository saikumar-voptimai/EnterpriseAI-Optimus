from functools import lru_cache
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://voptimai@localhost:5432/voptimai"
    openrouter_api_key: str = ""
    openrouter_models: str = "openai/gpt-4.1-mini"
    openrouter_default_model: str = "openai/gpt-4.1-mini"
    allow_external_ai: bool = False
    session_cookie_secure: bool = True
    session_ttl_hours: int = Field(default=12, ge=1, le=168)
    app_origin: str = "https://localhost"
    max_upload_bytes: int = Field(default=2097152, ge=1024, le=10485760)
    max_context_chars: int = Field(default=24000, ge=2000, le=100000)
    max_output_tokens: int = Field(default=2048, ge=128, le=16000)
    openrouter_timeout_seconds: int = Field(default=90, ge=10, le=120)
    worker_poll_seconds: int = Field(default=5, ge=1, le=60)
    job_lease_seconds: int = Field(default=180, ge=150, le=3600)
    openrouter_system1_model: str = ""
    bootstrap_token: str = ""
    credential_encryption_key: str = ""
    data_dir: str = "/app/data"
    knowledge_storage_dir: str = "/app/data/documents"
    agent_checkpoint_retention_days: int = Field(default=7, ge=1, le=90)
    agent_max_model_steps: int = Field(default=5, ge=1, le=20)
    agent_max_tool_calls: int = Field(default=8, ge=0, le=40)
    agent_max_attempts: int = Field(default=3, ge=1, le=5)
    agent_lease_seconds: int = Field(default=180, ge=150, le=3600)
    embeddings_enabled: bool = True
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=64, le=8192)
    embedding_batch_size: int = Field(default=32, ge=1, le=128)
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant_id: str = "organizations"
    connector_timeout_seconds: int = Field(default=30, ge=5, le=120)
    speech_api_key: str = ""
    speech_base_url: str = "https://api.groq.com/openai/v1"
    speech_model: str = "whisper-large-v3-turbo"
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    frontend_dist: str = str(Path(__file__).resolve().parents[2] / "frontend" / "dist")

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value):
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use postgresql+psycopg://")
        return value

    @field_validator("app_origin")
    @classmethod
    def origin_only(cls, value):
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise ValueError("APP_ORIGIN must be a single HTTP(S) origin without a path")
        return value.rstrip("/")

    @property
    def allowed_models(self):
        return list(
            dict.fromkeys(m.strip() for m in self.openrouter_models.split(",") if m.strip())
        )


@lru_cache
def get_settings():
    return Settings()
