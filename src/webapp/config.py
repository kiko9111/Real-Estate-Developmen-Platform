"""Settings, read once from the environment.

DATABASE_URL       SQLAlchemy URL. Default: a SQLite file in ./data/platform.db
SECRET_KEY         Signs the login cookie. Required outside development.
APP_ENV            "development" (default) or "production".
ANTHROPIC_API_KEY  Turns on the web market scan. Without it the team enters comparables by hand.
MARKET_SCAN_MODEL  Claude model used by the market scan. Default: claude-opus-5-5.
BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD
                   If set and no user exists yet, this admin is created at start-up.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _default_db() -> str:
    Path("data").mkdir(exist_ok=True)
    return "sqlite:///data/platform.db"


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL") or _default_db())
    app_env: str = field(default_factory=lambda: os.environ.get("APP_ENV", "development"))
    secret_key: str = field(default_factory=lambda: os.environ.get("SECRET_KEY", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    market_scan_model: str = field(default_factory=lambda: os.environ.get("MARKET_SCAN_MODEL", "claude-opus-5-5"))
    bootstrap_admin_email: str = field(default_factory=lambda: os.environ.get("BOOTSTRAP_ADMIN_EMAIL", ""))
    bootstrap_admin_password: str = field(default_factory=lambda: os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", ""))
    currency: str = "EGP"

    def __post_init__(self):
        if not self.secret_key:
            if self.app_env == "production":
                raise RuntimeError("SECRET_KEY must be set when APP_ENV=production")
            self.secret_key = secrets.token_urlsafe(32)   # dev only: logins reset on restart

    @property
    def market_scan_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def secure_cookies(self) -> bool:
        return self.app_env == "production"
