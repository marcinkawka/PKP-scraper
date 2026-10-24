from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class ConfigError(RuntimeError):
    """Required configuration is missing."""


@dataclass(frozen=True)
class ApiSettings:
    api_key: str
    base_url: str
    timeout_s: float = 30.0

    @classmethod
    def from_env(cls, env_file: Path = ENV_FILE) -> ApiSettings:
        load_dotenv(env_file)
        missing = [name for name in ("X_API_KEY", "PKP_API_BASE_URL") if not os.getenv(name)]
        if missing:
            raise ConfigError(f"Missing in {env_file.name}: {', '.join(missing)}")
        return cls(
            api_key=os.environ["X_API_KEY"],
            base_url=os.environ["PKP_API_BASE_URL"].rstrip("/"),
            timeout_s=float(os.getenv("PKP_API_TIMEOUT_S", cls.timeout_s)),
        )


@dataclass(frozen=True)
class DbSettings:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_env(cls, env_file: Path = ENV_FILE) -> DbSettings:
        load_dotenv(env_file)
        names = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
        missing = [name for name in names if not os.getenv(name)]
        if missing:
            raise ConfigError(f"Missing in {env_file.name}: {', '.join(missing)}")
        return cls(
            host=os.environ["DB_HOST"],
            port=int(os.environ["DB_PORT"]),
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
        )
