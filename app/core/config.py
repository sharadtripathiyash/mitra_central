"""Central configuration loaded from environment variables."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "Mitra Central"
    app_secret_key: str = "change-me"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = True

    # Auth (hardcoded Phase 1)
    auth_username: str = "admin"
    auth_password: str = "mfgpro"

    # Session
    session_cookie_name: str = "mitra_session"
    session_ttl_seconds: int = 28800

    # QAD
    # Default QAD domain (set to the Progress environment domain)
    qad_domain: str = "10USA"
    default_row_limit: int = 50

    # ODBC - defaults useful for local testing; override via env/.env
    odbc_dsn: str = "yashlocal"
    odbc_user: str = "admin"
    odbc_password: str = ""
    odbc_connection_string: str = ""

    # LLM — OpenAI (quality tasks: SQL gen, RAG answers, doc gen)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_embed_model: str = "text-embedding-3-large"

    # LLM — Groq (free fast tasks: classification, table-ID, routing)
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Qdrant
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection_apex: str = "qad_docs"
    qdrant_collection_custom: str = "qad_custom_docs"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
