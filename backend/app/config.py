"""Application configuration.

Infrastructure settings (DB, Redis, JWT, ports) are required env vars — they
must exist before the app can boot, and the app needs them to reach its own
data store. The **Nexus connection** (URL/username/password) is different:
it's managed at runtime via the dashboard and stored encrypted in the
database, so those env vars are **optional** — they act only as a bootstrap
default on first launch. After that, the dashboard-stored config wins.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Nexus connection (OPTIONAL — managed via dashboard, stored in DB) -
    # These act as bootstrap defaults for first launch. Once an admin saves
    # Nexus settings via the dashboard, the DB value takes precedence.
    NEXUS_URL: str = ""
    NEXUS_USERNAME: str = ""
    NEXUS_PASSWORD: str = ""
    NEXUS_VERIFY_SSL: bool = True

    # --- Database (v2 persistence: users/roles/permissions) --------------
    DATABASE_URL: str

    # --- Auth (JWT in httpOnly cookies) -----------------------------------
    JWT_SECRET: str
    # Encryption key for the Nexus password stored in the dashboard DB. If
    # unset, we derive one from JWT_SECRET so the app still boots (with a
    # warning) — set it explicitly in production.
    NEXUS_CONFIG_ENCRYPTION_KEY: str = ""
    JWT_ALGORITHM: str
    JWT_ACCESS_TTL_SECONDS: int
    JWT_REFRESH_TTL_SECONDS: int
    # Idle logout: if no request arrives for this many seconds, the refresh
    # endpoint refuses to issue a new access token (effectively logging the
    # user out). Independent of the refresh token's own TTL.
    SESSION_IDLE_TIMEOUT_SECONDS: int
    # Sets the ``Secure`` flag on auth cookies. MUST be true in production
    # behind TLS. Allow false for local HTTP testing.
    COOKIE_SECURE: bool
    FRONTEND_ORIGIN: str  # used for CORS + cookie domain scoping

    # --- Bootstrap admin (created on first startup) ----------------------
    BOOTSTRAP_ADMIN_USERNAME: str
    BOOTSTRAP_ADMIN_PASSWORD: str
    BOOTSTRAP_ADMIN_EMAIL: str

    # --- Cache (Redis) ----------------------------------------------------
    REDIS_URL: str
    CACHE_TTL_SECONDS: int

    # --- Deep Storage Analyzer tuning (Feature A) ------------------------
    ANALYZER_MAX_CONCURRENCY: int
    ANALYZER_REQUEST_TIMEOUT: int

    # --- Monitoring (v3: metrics + alerts) -------------------------------
    METRIC_COLLECTION_INTERVAL_SECONDS: int
    METRIC_RETENTION_DAYS: int

    # --- Retention scheduler (v4) ----------------------------------------
    # Time of day (HH:MM, server-local) at which the daily retention sweep
    # runs all enabled policies. Format: "HH:MM" 24h, e.g. "02:30".
    RETENTION_RUN_AT: str
    # --- Scanner (v4: Trivy/Grype) ---------------------------------------
    # How often to refresh vulnerability databases. Default 1 day.
    SCANNER_DB_UPDATE_INTERVAL_HOURS: int
    # Optional fixed time-of-day (HH:MM, server-local) for the scanner DB
    # refresh, e.g. "03:00". When set, the refresh runs once daily at this
    # time instead of every SCANNER_DB_UPDATE_INTERVAL_HOURS. Leave empty to
    # keep the interval behaviour.
    SCANNER_DB_UPDATE_AT: str = ""
    # When true, the scheduled refresh imports from the offline archive dir
    # (no network) instead of downloading. Use on air-gapped/restricted
    # networks. On-demand /db-import always works regardless of this flag.
    SCANNER_DB_OFFLINE_MODE: bool = False
    # Comma-separated scanner binaries to enable: "trivy", "grype". Order is
    # the order they run in when both are enabled.
    SCANNERS_ENABLED: str
    # Optional HTTP/HTTPS proxy for scanner DB downloads. Leave empty for
    # direct. Mirrors HTTP_PROXY/HTTPS_PROXY behaviour for the subprocess env.
    SCANNER_PROXY: str = ""

    # --- Backend server runtime ------------------------------------------
    BACKEND_HOST: str
    BACKEND_PORT: int
    LOG_LEVEL: str

    # ------------------------------------------------------------------
    # Derived / parsed values
    # ------------------------------------------------------------------
    @field_validator("NEXUS_URL", "FRONTEND_ORIGIN", "DATABASE_URL")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        """Normalise URLs so joining never produces ``//``."""
        return value.rstrip("/")

    @property
    def cors_origins(self) -> list[str]:
        """CORS origins parsed from FRONTEND_ORIGIN (single origin for now)."""
        return [origin.strip() for origin in self.FRONTEND_ORIGIN.split(",") if origin.strip()]

    @property
    def scanners_enabled(self) -> list[str]:
        """Enabled scanner binaries in run-order."""
        return [s.strip().lower() for s in self.SCANNERS_ENABLED.split(",") if s.strip()]

    @property
    def retention_time_of_day(self) -> tuple[int, int]:
        """Return (hour, minute) parsed from RETENTION_RUN_AT (HH:MM)."""
        try:
            hh, mm = self.RETENTION_RUN_AT.split(":")
            return (int(hh), int(mm))
        except (ValueError, AttributeError):
            return (2, 30)  # sane default if misconfigured

    @property
    def scanner_db_time_of_day(self) -> tuple[int, int] | None:
        """Return (hour, minute) from SCANNER_DB_UPDATE_AT, or None if unset.

        When None, the scanner DB loop uses the interval schedule instead.
        """
        raw = (self.SCANNER_DB_UPDATE_AT or "").strip()
        if not raw:
            return None
        try:
            hh, mm = raw.split(":")
            h, m = int(hh), int(mm)
            if 0 <= h <= 23 and 0 <= m <= 59:
                return (h, m)
        except (ValueError, AttributeError):
            pass
        return None

    @property
    def access_cookie(self) -> dict:
        """Cookie kwargs for the access-token cookie."""
        return self._cookie_kwargs("access_token", self.JWT_ACCESS_TTL_SECONDS)

    @property
    def refresh_cookie(self) -> dict:
        """Cookie kwargs for the refresh-token cookie."""
        return self._cookie_kwargs("refresh_token", self.JWT_REFRESH_TTL_SECONDS)

    def _cookie_kwargs(self, key: str, max_age: int) -> dict:
        return {
            "key": key,
            "httponly": True,
            "secure": self.COOKIE_SECURE,
            "samesite": "lax",
            "max_age": max_age,
            # Path covers the whole API so every protected endpoint can read the
            # cookie, not just /api/auth. The browser only sends the cookie for
            # requests whose path starts with this prefix.
            "path": "/api",
        }


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance (validation runs once)."""
    return Settings()  # type: ignore[call-arg]
