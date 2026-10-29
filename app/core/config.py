from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOB_AGENT_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = Field(default="AI Job Agent", min_length=1, max_length=100)
    environment: Literal["development", "test", "production"] = "development"
    # Business authentication and external deployment are outside stage 1.
    host: Literal["127.0.0.1", "::1"] = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: SecretStr | None = None

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value):
        if value is not None:
            try:
                url = make_url(value.get_secret_value())
                if url.drivername != "postgresql+psycopg" or not url.database:
                    raise ValueError
            except Exception:
                raise ValueError("database_url 必须为 postgresql+psycopg 连接地址") from None
        return value
