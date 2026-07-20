"""Central configuration, read once from the environment.

Keep this dependency-free (stdlib only) so it can be imported from the one-time
auth_setup.py script as well as the long-running worker.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    # --- X (Twitter) OAuth 2.0 user-context app ---
    # Create an app at https://developer.x.com, enable OAuth 2.0, set the app
    # type to "Web App / Automated" (confidential) or "Native" (public).
    x_client_id: str = field(default_factory=lambda: os.getenv("X_CLIENT_ID", ""))
    # Optional: only set for a *confidential* client. Public (PKCE-only) clients
    # leave this blank and we authenticate with the client_id in the body.
    x_client_secret: str = field(default_factory=lambda: os.getenv("X_CLIENT_SECRET", ""))
    # Redirect URI must match EXACTLY what you registered on the X app.
    x_redirect_uri: str = field(
        default_factory=lambda: os.getenv("X_REDIRECT_URI", "http://127.0.0.1:8721/callback")
    )
    x_scopes: str = field(
        default_factory=lambda: os.getenv("X_SCOPES", "tweet.read users.read offline.access")
    )
    # Bootstrap refresh token produced by auth_setup.py. Seeded into the DB on
    # first run, after which the DB copy (which rotates) is authoritative.
    x_refresh_token_seed: str = field(default_factory=lambda: os.getenv("X_REFRESH_TOKEN", ""))

    # Swap the source timeline by pointing at a List instead of the home
    # timeline. Leave blank to use the authenticated user's home timeline.
    x_list_id: str = field(default_factory=lambda: os.getenv("X_LIST_ID", ""))
    max_results: int = field(default_factory=lambda: int(os.getenv("X_MAX_RESULTS", "100")))
    # Safety cap on pagination so a first-ever run can't loop forever.
    max_pages: int = field(default_factory=lambda: int(os.getenv("X_MAX_PAGES", "10")))

    # --- Classification LLM (provider-selectable) ---
    # "groq" (default, free tier) or "anthropic". Same JSON contract either way,
    # so switching providers is just this env var + the matching API key.
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "groq").lower())

    # Groq (OpenAI-compatible, free tier): key from https://console.groq.com/keys
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))

    # Anthropic (optional fallback): key from https://console.anthropic.com
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    claude_model: str = field(
        default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
    )

    # --- Storage ---
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "./data/hiring.db"))

    # --- Email digest (stdlib smtplib; works with any SMTP provider) ---
    smtp_host: str = field(default_factory=lambda: os.getenv("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    smtp_pass: str = field(default_factory=lambda: os.getenv("SMTP_PASS", ""))
    smtp_starttls: bool = field(default_factory=lambda: _bool("SMTP_STARTTLS", True))
    email_from: str = field(default_factory=lambda: os.getenv("EMAIL_FROM", os.getenv("SMTP_USER", "")))
    email_to: str = field(default_factory=lambda: os.getenv("EMAIL_TO", ""))

    # Skip actually sending mail (useful for local testing / dry runs).
    email_enabled: bool = field(default_factory=lambda: _bool("EMAIL_ENABLED", True))

    def require_worker(self) -> None:
        """Fail fast with a clear message if the worker is missing must-haves."""
        missing = []
        if not self.x_client_id:
            missing.append("X_CLIENT_ID")
        if self.llm_provider == "groq" and not self.groq_api_key:
            missing.append("GROQ_API_KEY")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if missing:
            raise RuntimeError(
                "Missing required env vars: " + ", ".join(missing)
            )


config = Config()
