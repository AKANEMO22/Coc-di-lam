"""Gateway settings, read from the environment and the repo-root .env file."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
PLACEHOLDER_SECRETS = {
    "local-compose-development-secret-change-before-production",
    "replace-with-core-jwt-secret",
    "local-idq-api-token-change-before-production",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # Core AI (core_agent). The gateway signs its own service tokens with the core's JWT secret.
    core_url: str = "http://127.0.0.1:8001"
    core_jwt_secret: str = "local-compose-development-secret-change-before-production"
    core_jwt_algorithm: str = "HS256"
    core_timeout_sec: float = 60.0
    max_answer_chars: int = Field(default=20_000, gt=0)  # = MAX_ANSWER_CHARS of the core (answer + attachments)

    # Speech service (core-tts-stt)
    speech_url: str = "http://127.0.0.1:8010"
    speech_service_token: str = ""  # = SERVICE_TOKEN of the speech service, if it sets one
    stt_language: str = "vi-VN"  # comma-separated BCP-47, or "auto"
    stt_final_timeout_sec: float = 10.0
    tts_voice: str = ""  # empty = speech service default
    tts_enabled: bool = True

    # ID_Q service (RAG_CocDiLam): CV/form intake and knowledge packs. Core fetches the pack by id.
    rag_url: str = "http://127.0.0.1:8200"
    rag_api_token: str = "local-idq-api-token-change-before-production"  # = RAG_API_TOKEN (RAG_IDQ_API_TOKEN) of the ID_Q service
    rag_timeout_sec: float = 180.0  # preparation may include a bounded web search for the JD
    rag_prepare_top_k: int = 20
    rag_web_search_mode: str = "auto"  # auto | off | refresh

    # IT Job Sync service
    job_sync_url: str = "http://127.0.0.1:8050"

    # Gateway
    app_env: str = "development"
    cors_origins: str = "*"
    max_upload_bytes: int = 12 * 1024 * 1024
    trust_proxy_headers: bool = False

    # Sign-in for the browser.
    #   ""/"off": no sign-in, every session is visible to everyone (local development)
    #   "open":    any username, no password; each username is its own account (sessions, CV, reports, streak)
    #   "student": student id (+ optional class access code); also what AUTH_REQUIRED=true selects
    auth_mode: str = ""
    auth_required: bool = False
    auth_jwt_secret: str = ""
    auth_ttl_sec: int = Field(default=8 * 3600, gt=0)
    auth_issuer: str = "interview-gateway"
    access_code: str = ""
    student_id_pattern: str = ""
    database_path: str = "data/gateway.db"

    metrics_token: str = ""
    rate_login_per_min: int = Field(default=60, ge=0)
    rate_create_per_min: int = Field(default=6, ge=0)
    rate_ws_per_min: int = Field(default=120, ge=0)
    quota_sessions_per_day: int = Field(default=0, ge=0)
    admin_token: str = ""

    @model_validator(mode="after")
    def check_security(self):
        mode = self.auth_mode.strip().lower()
        if mode in ("", "off", "none"):
            mode = "student" if self.auth_required else "off"
        elif self.auth_required and mode == "open":
            mode = "student"  # an explicit AUTH_REQUIRED=true never gets weakened to open sign-in
        if mode not in ("off", "open", "student"):
            raise ValueError("AUTH_MODE phải là off, open hoặc student")
        self.auth_mode = mode
        self.auth_required = mode != "off"
        if self.student_id_pattern:
            try:
                re.compile(self.student_id_pattern)
            except re.error as exc:
                raise ValueError(f"STUDENT_ID_PATTERN không hợp lệ: {exc}") from exc
        # Open sign-in may leave the secret empty: the gateway then keeps a generated one next to its database.
        if mode == "student" and len(self.auth_jwt_secret) < 32:
            raise ValueError("AUTH_REQUIRED=true cần AUTH_JWT_SECRET dài ít nhất 32 ký tự")
        if mode == "open" and self.auth_jwt_secret and len(self.auth_jwt_secret) < 32:
            raise ValueError("AUTH_JWT_SECRET phải dài ít nhất 32 ký tự")
        if self.app_env.lower() == "production":
            if self.core_jwt_secret in PLACEHOLDER_SECRETS or len(self.core_jwt_secret) < 32:
                raise ValueError("APP_ENV=production cần CORE_JWT_SECRET thật (>= 32 ký tự, trùng với core)")
            if self.rag_api_token in PLACEHOLDER_SECRETS or len(self.rag_api_token) < 16:
                raise ValueError("APP_ENV=production cần RAG_API_TOKEN thật (trùng với dịch vụ RAG)")
            if "*" in split_csv(self.cors_origins) or not split_csv(self.cors_origins):
                raise ValueError("APP_ENV=production cần CORS_ORIGINS liệt kê địa chỉ giao diện, không dùng *")
        return self

    @property
    def core_ws_url(self) -> str:
        return _ws(self.core_url)

    @property
    def speech_ws_url(self) -> str:
        return _ws(self.speech_url)


def _ws(url: str) -> str:
    url = url.rstrip("/")
    return "wss://" + url[8:] if url.startswith("https://") else "ws://" + url.removeprefix("http://")


def split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
