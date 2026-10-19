"""Small explicit configuration surface; never import secrets into the frontend."""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    db_path: str = str(ROOT / "data" / "support.db")
    llm_provider: str = "mock"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 8.0
    llm_max_attempts: int = 2
    session_ttl_seconds: int = 3600
    reviewer_api_key: str = ""

    def __post_init__(self):
        if self.llm_provider not in {"mock", "openai_compatible"}:
            raise ValueError("LLM_PROVIDER must be mock or openai_compatible")
        if self.llm_provider != "mock" and not self.llm_model.strip():
            raise ValueError("LLM_MODEL is required for the real provider")
        if not 0 < self.llm_timeout_seconds <= 30 or not 1 <= self.llm_max_attempts <= 3:
            raise ValueError("Use a timeout <=30 seconds and 1–3 model attempts")
        if self.session_ttl_seconds <= 0:
            raise ValueError("SESSION_TTL_SECONDS must be positive")

    @classmethod
    def from_env(cls):
        return cls(
            db_path=os.getenv("DATABASE_PATH", cls.db_path),
            llm_provider=os.getenv("LLM_PROVIDER", "mock"),
            llm_base_url=os.getenv("LLM_BASE_URL", cls.llm_base_url).rstrip("/"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", ""),
            llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "8")),
            llm_max_attempts=int(os.getenv("LLM_MAX_ATTEMPTS", "2")),
            session_ttl_seconds=int(os.getenv("SESSION_TTL_SECONDS", "3600")),
            reviewer_api_key=os.getenv("REVIEWER_API_KEY", ""),
        )
