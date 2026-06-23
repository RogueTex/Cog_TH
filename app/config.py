"""Application configuration loaded from environment variables.

All settings are read once at import time. Secrets are never logged or echoed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _clean(value: str | None) -> str | None:
    """Strip whitespace and treat empty strings as unset."""
    if value is None:
        return None
    value = value.strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the issue runner service."""

    devin_api_key: str | None = field(default_factory=lambda: _clean(os.getenv("DEVIN_API_KEY")))
    devin_org_id: str | None = field(default_factory=lambda: _clean(os.getenv("DEVIN_ORG_ID")))
    devin_api_base: str = field(
        default_factory=lambda: _clean(os.getenv("DEVIN_API_BASE")) or "https://api.devin.ai"
    )

    github_token: str | None = field(default_factory=lambda: _clean(os.getenv("GITHUB_TOKEN")))
    github_repo: str = field(
        default_factory=lambda: _clean(os.getenv("GITHUB_REPO")) or "RogueTex/superset"
    )
    github_api_base: str = field(
        default_factory=lambda: _clean(os.getenv("GITHUB_API_BASE")) or "https://api.github.com"
    )

    db_path: str = field(
        default_factory=lambda: _clean(os.getenv("DB_PATH")) or "data/devin_issue_runner.db"
    )

    # Default ACU ceiling for real sessions.
    max_acu_limit: int = field(
        default_factory=lambda: int(_clean(os.getenv("MAX_ACU_LIMIT")) or "10")
    )

    request_timeout: float = field(
        default_factory=lambda: float(_clean(os.getenv("REQUEST_TIMEOUT")) or "30")
    )
    webhook_label: str = field(
        default_factory=lambda: _clean(os.getenv("WEBHOOK_LABEL")) or "devin-remediate"
    )

    @property
    def devin_configured(self) -> bool:
        return bool(self.devin_api_key and self.devin_org_id)

    @property
    def github_configured(self) -> bool:
        return bool(self.github_token)

    def missing_for_real_mode(self) -> list[str]:
        """Return env vars required for real mode that are not set."""
        missing: list[str] = []
        if not self.devin_api_key:
            missing.append("DEVIN_API_KEY")
        if not self.devin_org_id:
            missing.append("DEVIN_ORG_ID")
        if not self.github_token:
            missing.append("GITHUB_TOKEN")
        return missing


def get_settings() -> Settings:
    """Build a fresh Settings instance from the current environment.

    Not cached so tests can mutate the environment between calls.
    """
    return Settings()


# Shared tags applied to every Devin session created by this automation.
DEFAULT_TAGS: tuple[str, ...] = ("devin-workflow", "superset", "devin-remediate")
