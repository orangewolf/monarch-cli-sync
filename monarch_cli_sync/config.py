"""Config loading: ~/.config/monarch-cli-sync/config.toml with env var fallback."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, model_validator
from dotenv import load_dotenv

load_dotenv()

CONFIG_DIR = Path(os.environ.get("MONARCH_CONFIG_DIR", "~/.config/monarch-cli-sync")).expanduser()
CONFIG_FILE = CONFIG_DIR / "config.toml"


class AmazonConfig(BaseModel):
    username: str = ""
    password: str = ""
    otp_secret_key: str = ""
    request_delay_seconds: float = 1.0

    @model_validator(mode="before")
    @classmethod
    def apply_env_vars(cls, values: dict) -> dict:
        values.setdefault("username", os.environ.get("AMAZON_USERNAME", ""))
        values.setdefault("password", os.environ.get("AMAZON_PASSWORD", ""))
        values.setdefault("otp_secret_key", os.environ.get("AMAZON_OTP_SECRET_KEY", ""))
        return values


class MonarchConfig(BaseModel):
    email: str = ""
    password: str = ""
    mfa_secret_key: str = ""

    @model_validator(mode="before")
    @classmethod
    def apply_env_vars(cls, values: dict) -> dict:
        values.setdefault("email", os.environ.get("MONARCH_EMAIL", ""))
        values.setdefault("password", os.environ.get("MONARCH_PASSWORD", ""))
        values.setdefault("mfa_secret_key", os.environ.get("MONARCH_MFA_SECRET_KEY", ""))
        return values


class SyncConfig(BaseModel):
    default_days: int = 30
    date_window_days: int = 7
    force: bool = False


class AppConfig(BaseModel):
    amazon: AmazonConfig = Field(default_factory=AmazonConfig)
    monarch: MonarchConfig = Field(default_factory=MonarchConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)


def load_config(config_file: Optional[Path] = None) -> AppConfig:
    """Load config from TOML file (if present), overlaid with env vars."""
    path = config_file or CONFIG_FILE
    raw: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    return AppConfig.model_validate(raw)
