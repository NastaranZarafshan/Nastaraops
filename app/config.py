from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "production", "test"] = "development"
    api_key: str = "dev-only-change-me"
    database_url: str = "postgresql+asyncpg://agents:local-development-only@localhost:5432/agents"
    checkpoint_dsn: str = "postgresql://agents:local-development-only@localhost:5432/agents"
    redis_url: str = "redis://localhost:6379/0"
    mcp_url: str = "http://localhost:8001/mcp"
    llm_mode: Literal["mock", "ollama"] = "mock"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"
    ollama_embed_model: str = "bge-m3"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "agentops_knowledge"
    rag_top_k: int = Field(default=5, ge=1, le=12)
    rag_score_threshold: float = Field(default=0.35, ge=0, le=1)
    rag_chunk_size: int = Field(default=1200, ge=300, le=4000)
    rag_chunk_overlap: int = Field(default=180, ge=0, le=800)
    model_timeout_seconds: int = Field(default=120, ge=10, le=600)
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_poll_seconds: int = Field(default=60, ge=15, le=3600)
    max_upload_bytes: int = Field(default=5_242_880, ge=1024, le=50_000_000)

    @model_validator(mode="after")
    def secure_production(self):
        if self.app_env == "production" and (len(self.api_key) < 32 or self.api_key in {"dev-only-change-me", "replace-this-with-a-long-random-secret"}):
            raise ValueError("Production API_KEY must be a unique secret of at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
