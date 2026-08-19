"""Config loading: ~/.config/monarch-cli-sync/config.toml with env var fallback."""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, model_validator
from dotenv import load_dotenv

load_dotenv()

CONFIG_DIR = Path(os.environ.get("MONARCH_CONFIG_DIR", "~/.config/monarch-cli-sync")).expanduser()
CONFIG_FILE = CONFIG_DIR / "config.toml"

logger = logging.getLogger(__name__)


class AmazonAccountConfig(BaseModel):
    """Configuration for a single Amazon account."""

    index: int = 0          # 1-based; 0 = unset, auto-assigned by AmazonConfig
    label: str = ""         # human label; defaults to "account-{index}"
    username: str
    password: str
    otp_secret_key: str = ""
    request_delay_seconds: float = 1.0
    captcha_solver: str = ""
    captcha_api_key: str = ""

    @property
    def cookie_file_stem(self) -> str:
        """Stem (no extension) of the per-account cookie file.

        Single-account backward-compat path: index=1, label='account-1'
        uses the original 'amazon_cookies' stem so no migration is needed.
        """
        if self.index == 1 and self.label == "account-1":
            return "amazon_cookies"
        return f"amazon_cookies_{self.label}"


def _discover_numbered_accounts(
    captcha_solver: str = "",
    captcha_api_key: str = "",
    request_delay_seconds: float = 1.0,
) -> list[AmazonAccountConfig]:
    """Scan AMAZON_USERNAME_N env vars and build a numbered account list.

    Stops at the first gap.  Logs a warning if higher-indexed vars exist
    beyond the gap (indicating a likely misconfiguration).
    """
    discovered: list[AmazonAccountConfig] = []
    n = 1
    while True:
        username = os.environ.get(f"AMAZON_USERNAME_{n}", "")
        if not username:
            # Check a small window ahead for a gap warning
            if n > 1 and any(
                os.environ.get(f"AMAZON_USERNAME_{m}", "")
                for m in range(n + 1, n + 5)
            ):
                logger.warning(
                    "Gap in Amazon account numbering at index %d; stopping "
                    "discovery. Check your AMAZON_USERNAME_* env vars.",
                    n,
                )
            break
        password = os.environ.get(f"AMAZON_PASSWORD_{n}", "")
        label = os.environ.get(f"AMAZON_LABEL_{n}", "") or f"account-{n}"
        otp_secret_key = os.environ.get(f"AMAZON_OTP_SECRET_{n}", "")
        discovered.append(
            AmazonAccountConfig(
                index=n,
                label=label,
                username=username,
                password=password,
                otp_secret_key=otp_secret_key,
                request_delay_seconds=request_delay_seconds,
                captcha_solver=captcha_solver,
                captcha_api_key=captcha_api_key,
            )
        )
        n += 1
    return discovered


class AmazonConfig(BaseModel):
    # Multi-account list — populated by _resolve_accounts
    accounts: list[AmazonAccountConfig] = Field(default_factory=list)

    # Legacy flat fields — kept for backward compat (TOML [amazon] section and
    # direct attribute access in existing code/tests)
    username: str = ""
    password: str = ""
    otp_secret_key: str = ""
    request_delay_seconds: float = 1.0
    captcha_solver: str = ""
    captcha_api_key: str = ""

    @model_validator(mode="before")
    @classmethod
    def apply_env_vars(cls, values: dict) -> dict:
        values.setdefault("username", os.environ.get("AMAZON_USERNAME", ""))
        values.setdefault("password", os.environ.get("AMAZON_PASSWORD", ""))
        values.setdefault("otp_secret_key", os.environ.get("AMAZON_OTP_SECRET_KEY", ""))
        values.setdefault("captcha_solver", os.environ.get("AMAZON_CAPTCHA_SOLVER", ""))
        values.setdefault("captcha_api_key", os.environ.get("AMAZON_CAPTCHA_API_KEY", ""))
        return values

    @model_validator(mode="after")
    def _resolve_accounts(self) -> "AmazonConfig":
        """Build the accounts list from env vars, TOML, or legacy flat fields.

        Priority (highest → lowest):
        1. Numbered env vars  AMAZON_USERNAME_N  (override everything)
        2. TOML [[amazon.accounts]] array        (already in self.accounts)
        3. Legacy flat fields  AMAZON_USERNAME   (single-account compat)
        """
        # 1. Numbered env vars always win
        numbered = _discover_numbered_accounts(
            captcha_solver=self.captcha_solver,
            captcha_api_key=self.captcha_api_key,
            request_delay_seconds=self.request_delay_seconds,
        )
        if numbered:
            self.accounts = numbered
            return self

        # 2. TOML [[amazon.accounts]] already populated self.accounts via
        #    Pydantic field validation — assign sequential indexes if missing
        if self.accounts:
            fixed: list[AmazonAccountConfig] = []
            for i, acct in enumerate(self.accounts, start=1):
                idx = acct.index if acct.index != 0 else i
                lbl = acct.label if acct.label else f"account-{idx}"
                fixed.append(
                    acct.model_copy(
                        update={
                            "index": idx,
                            "label": lbl,
                            "captcha_solver": acct.captcha_solver or self.captcha_solver,
                            "captcha_api_key": acct.captcha_api_key or self.captcha_api_key,
                            "request_delay_seconds": (
                                acct.request_delay_seconds
                                if acct.request_delay_seconds != 1.0
                                else self.request_delay_seconds
                            ),
                        }
                    )
                )
            self.accounts = fixed
            return self

        # 3. Legacy single-account flat fields
        if self.username:
            self.accounts = [
                AmazonAccountConfig(
                    index=1,
                    label="account-1",
                    username=self.username,
                    password=self.password,
                    otp_secret_key=self.otp_secret_key,
                    request_delay_seconds=self.request_delay_seconds,
                    captcha_solver=self.captcha_solver,
                    captcha_api_key=self.captcha_api_key,
                )
            ]

        return self


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
