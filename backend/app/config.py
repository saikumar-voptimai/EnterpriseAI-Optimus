from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_TIERS = (("fast", "Fast"), ("standard", "Standard"), ("deep", "Deep"))


class Settings(BaseSettings):
    # Native runs read the repository-root .env; a backend/.env overrides it.
    # Compose passes the same values as process environment, which wins over both.
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env", ".env"), extra="ignore")
    database_url: str = "postgresql+psycopg://voptimai@localhost:5432/voptimai"
    openrouter_api_key: str = ""
    openrouter_models: str = "openai/gpt-4.1-mini"
    openrouter_default_model: str = "openai/gpt-4.1-mini"
    # Optional three-level model ladder. When any tier is set, the tiers form the
    # allowlist (plus explicit OPENROUTER_MODELS), Standard is the default and Fast
    # handles classification/drafting unless those settings are given explicitly.
    openrouter_model_fast: str = ""
    openrouter_model_standard: str = ""
    openrouter_model_deep: str = ""
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
    # pgvector keeps semantic search inside PostgreSQL. qdrant serves similarity
    # search from Qdrant; PostgreSQL still holds each vector and re-authorizes hits.
    vector_backend: Literal["pgvector", "qdrant"] = "pgvector"
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = Field(default="optimus_knowledge", pattern=r"^[A-Za-z0-9_-]{1,64}$")
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

    @model_validator(mode="after")
    def resolve_model_tiers(self):
        tiers = {tier: getattr(self, f"openrouter_model_{tier}").strip() for tier, _ in MODEL_TIERS}
        configured = [model for model in tiers.values() if model]
        if not configured:
            return self
        explicit = self.model_fields_set
        extra = self.openrouter_models.split(",") if "openrouter_models" in explicit else []
        models = dict.fromkeys(m.strip() for m in [*configured, *extra] if m.strip())
        self.openrouter_models = ",".join(models)
        if "openrouter_default_model" not in explicit or not self.openrouter_default_model:
            self.openrouter_default_model = tiers["standard"] or configured[0]
        if not self.openrouter_system1_model:
            self.openrouter_system1_model = tiers["fast"] or self.openrouter_default_model
        return self

    @property
    def allowed_models(self):
        return list(
            dict.fromkeys(m.strip() for m in self.openrouter_models.split(",") if m.strip())
        )

    @property
    def model_choices(self):
        """Allowlisted models with tier labels for selection menus."""
        labels = {}
        for tier, label in reversed(MODEL_TIERS):
            model = getattr(self, f"openrouter_model_{tier}").strip()
            if model:
                labels[model] = f"{label} · {model}"
        return [{"id": model, "label": labels.get(model, model)} for model in self.allowed_models]


@lru_cache
def get_settings():
    return Settings()
