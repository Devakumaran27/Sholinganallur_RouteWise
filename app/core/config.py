import os
import re
from typing import Any, Dict, List, Optional, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application configuration settings loaded from environment variables
    or a .env file using Pydantic Settings.
    """

    # Project metadata
    PROJECT_NAME: str = "Sholinganallur_RouteWise"
    API_V1_PREFIX: str = "/api/v1"

    # Server & Environment
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # API Keys & Database Secrets (loaded from environment)
    GEMINI_API_KEY: Optional[str] = None
    DATABASE_URL: Optional[str] = None

    # Database Connection Pool Settings
    DB_POOL_MIN_SIZE: int = 2
    DB_POOL_MAX_SIZE: int = 10
    DB_POOL_TIMEOUT: float = 30.0
    DB_POOL_MAX_IDLE: float = 300.0

    # CORS Allowed Origins
    CORS_ORIGINS: Union[List[str], str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://192.168.56.1:5173",
        "http://192.168.88.11:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    # Generative AI & Vector Embeddings Specification
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    EMBEDDING_DIMENSIONS: int = 768
    EMBEDDING_TASK_TYPE: str = "RETRIEVAL_DOCUMENT"
    EMBEDDING_BATCH_SIZE: int = 20
    MAX_DAILY_REQUEST_BUDGET: int = 200
    MAX_TPM_SAFETY_LIMIT: int = 24000
    MAX_RETRY_ATTEMPTS: int = 3

    # Gemini Chat & Grounded Question Answering Specification
    GEMINI_CHAT_MODEL: str = "gemini-3.5-flash-lite"
    GEMINI_CHAT_TEMPERATURE: float = 0.2
    GEMINI_CHAT_MAX_TOKENS: int = 2048

    # Backwards-compatibility alias
    @property
    def EMBEDDING_MAX_RETRIES(self) -> int:
        return self.MAX_RETRY_ATTEMPTS

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @field_validator("EMBEDDING_MODEL", mode="before")
    @classmethod
    def validate_embedding_model(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError(f"EMBEDDING_MODEL must be a string, got {type(v).__name__}")
        cleaned = v.strip()
        # Normalize "models/gemini-embedding-001" to "gemini-embedding-001"
        if cleaned.startswith("models/"):
            cleaned = cleaned[len("models/"):]
        allowed_models = {"gemini-embedding-001"}
        if cleaned not in allowed_models:
            raise ValueError(
                f"Invalid EMBEDDING_MODEL '{v}'. Allowed models: {sorted(allowed_models)}. "
                "Note: 'text-embedding-004' was shut down on January 14, 2026 and is not supported."
            )
        return cleaned

    @field_validator("GEMINI_CHAT_MODEL", mode="before")
    @classmethod
    def validate_chat_model(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError(f"GEMINI_CHAT_MODEL must be a string, got {type(v).__name__}")
        cleaned = v.strip()
        if cleaned.startswith("models/"):
            cleaned = cleaned[len("models/"):]
        return cleaned

    @field_validator("EMBEDDING_DIMENSIONS")
    @classmethod
    def validate_embedding_dimensions(cls, v: int) -> int:
        if v != 768:
            raise ValueError(
                f"Invalid EMBEDDING_DIMENSIONS '{v}'. Must be exactly 768 for the RouteWise "
                "pgvector vector space."
            )
        return v

    @field_validator("EMBEDDING_TASK_TYPE")
    @classmethod
    def validate_embedding_task_type(cls, v: str) -> str:
        allowed_tasks = {
            "RETRIEVAL_DOCUMENT",
            "RETRIEVAL_QUERY",
            "SEMANTIC_SIMILARITY",
            "CLASSIFICATION",
            "CLUSTERING",
        }
        if v not in allowed_tasks:
            raise ValueError(
                f"Invalid EMBEDDING_TASK_TYPE '{v}'. Allowed task types: {sorted(allowed_tasks)}."
            )
        return v

    @field_validator("EMBEDDING_BATCH_SIZE")
    @classmethod
    def validate_batch_size(cls, v: int) -> int:
        if not (1 <= v <= 50):
            raise ValueError(
                f"EMBEDDING_BATCH_SIZE must be between 1 and 50 chunks, got {v}."
            )
        return v

    @field_validator("MAX_DAILY_REQUEST_BUDGET")
    @classmethod
    def validate_daily_budget(cls, v: int) -> int:
        if not (1 <= v <= 1000):
            raise ValueError(
                f"MAX_DAILY_REQUEST_BUDGET must be between 1 and 1000 requests (Google AI Studio "
                f"Free Tier limit), got {v}."
            )
        return v

    @field_validator("MAX_TPM_SAFETY_LIMIT")
    @classmethod
    def validate_tpm_limit(cls, v: int) -> int:
        if not (1000 <= v <= 30000):
            raise ValueError(
                f"MAX_TPM_SAFETY_LIMIT must be between 1,000 and 30,000 TPM (Google AI Studio "
                f"Free Tier limit), got {v}."
            )
        return v

    @field_validator("MAX_RETRY_ATTEMPTS")
    @classmethod
    def validate_max_retries(cls, v: int) -> int:
        if not (0 <= v <= 10):
            raise ValueError(
                f"MAX_RETRY_ATTEMPTS must be between 0 and 10 attempts, got {v}."
            )
        return v

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            v_clean = v.strip()
            if v_clean.startswith("[") and v_clean.endswith("]"):
                import json
                try:
                    return json.loads(v_clean)
                except Exception:
                    pass
            return [i.strip() for i in v_clean.split(",") if i.strip()]
        elif isinstance(v, (list, set, tuple)):
            return [str(i).strip() for i in v if str(i).strip()]
        return [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://192.168.56.1:5173",
            "http://192.168.88.11:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]

    @field_validator("DB_POOL_MIN_SIZE")
    @classmethod
    def validate_pool_min_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"DB_POOL_MIN_SIZE must be >= 1, got {v}")
        return v

    @field_validator("DB_POOL_MAX_SIZE")
    @classmethod
    def validate_pool_max_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"DB_POOL_MAX_SIZE must be >= 1, got {v}")
        return v

    def __repr__(self) -> str:
        """
        Custom representation that masks all sensitive credentials (API keys, URLs, tokens).
        """
        masked_dict = {}
        secret_patterns = re.compile(r"(KEY|SECRET|PASSWORD|TOKEN|URL)", re.IGNORECASE)
        for key, val in self.__dict__.items():
            if secret_patterns.search(key) and val is not None:
                masked_dict[key] = "***REDACTED***"
            else:
                masked_dict[key] = val
        return f"Settings({masked_dict})"

    def __str__(self) -> str:
        return self.__repr__()


# Instantiate singleton settings instance
settings = Settings()
