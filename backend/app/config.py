"""Application configuration.

Infrastructure settings (DB, Redis, JWT, ports) are required env vars — they
must exist before the app can boot, and the app needs them to reach its own
data store. The **Nexus connection** (URL/username/password) is different:
it's managed at runtime via the dashboard and stored encrypted in the
database, so those env vars are **optional** — they act only as a bootstrap
default on first launch. After that, the dashboard-stored config wins.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Values that ship as copy-paste-ready placeholders in .env.example. A secret
# equal to one of these means the operator deployed the example file
# unedited — fail startup loudly rather than run with a guessable JWT signing
# key or a published admin password.
_PLACEHOLDER_SECRETS = {
    "change-me", "changeme", "password", "admin", "secret",
    "generate-a-32-byte-hex-secret", "replace-me", "replace_me",
    "replace-with-a-strong-password", "replace_with_a_strong_password",
    "replace-with-openssl-rand-hex-32", "replace_with_openssl_rand_hex_32",
    "your-secret-here", "example",
}


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
    # NOTE: there is deliberately no Docker registry URL or port setting. Each
    # Docker repository's connector endpoint is discovered from Nexus at scan
    # time (app/services/scanning/registry.py), so adding repositories or moving a
    # connector port needs no configuration change here or in the UI.

    # --- Database (v2 persistence: users/roles/permissions) --------------
    DATABASE_URL: str

    # --- Auth (JWT in httpOnly cookies) -----------------------------------
    JWT_SECRET: str
    # Encryption key for the Nexus password stored in the dashboard DB.
    # REQUIRED and distinct from JWT_SECRET: these two secrets protect
    # different things (session signing vs. data at rest) and must be
    # rotatable independently. See
    # security/findings/medium/MED-01-encryption-key-derived-from-jwt-secret.md.
    NEXUS_CONFIG_ENCRYPTION_KEY: str
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

    # --- Scan triggers (event-driven only) --------------------------------
    # Optional bootstrap value for the Nexus webhook secret. Leave empty and the
    # backend generates one on first use; read it from GET /api/scan/webhook and
    # paste it into the Nexus webhook capability.
    NEXUS_WEBHOOK_SECRET: str = ""
    # Fallback watcher for deployments without webhooks: how often (seconds) to
    # check enabled repositories for images the ledger has never seen. This
    # compares metadata only and scans nothing that is already known. Set to 0
    # to disable it entirely and rely purely on webhooks.
    SCAN_PUSH_POLL_SECONDS: int = 60

    # --- Backup archive (byte-level full/selective backup) ---------------
    # Directory the backend writes archive runs to — the dedicated backup
    # volume is mounted here in docker-compose.yml.
    BACKUP_OUTPUT_DIR: str = "/app/backups"
    # Abort a run (rather than fill the volume to zero) if free space under
    # BACKUP_OUTPUT_DIR drops below this many bytes. Default 512MB.
    BACKUP_MIN_FREE_BYTES: int = 536_870_912

    # --- Outbound request guard (SSRF) ------------------------------------
    # Hosts the backend is permitted to make user-directed outbound requests
    # to even though they resolve to a private/loopback/link-local address —
    # i.e. legitimate on-prem alert webhooks and sync targets. Comma-separated
    # hostnames or bare IPs, e.g. "nexus.internal,10.0.0.5". Empty (default)
    # means: no private destinations at all. See app/core/outbound.py and
    # security/findings/medium/MED-04-alert-webhook-ssrf.md.
    OUTBOUND_ALLOWED_HOSTS: str = ""

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

    @field_validator("JWT_SECRET")
    @classmethod
    def _reject_placeholder_jwt_secret(cls, value: str) -> str:
        """Refuse to boot with the .env.example placeholder or a weak secret.

        JWT_SECRET signs every access/refresh token. A fixed, guessable value
        (or the literal placeholder shipped in .env.example) lets anyone forge
        a valid session for any user, including admin — this must fail fast at
        startup, not just log a warning that's easy to miss.
        """
        if value.strip().lower() in _PLACEHOLDER_SECRETS:
            raise ValueError(
                "JWT_SECRET is still set to the .env.example placeholder. "
                "Generate a real one: `openssl rand -hex 32`."
            )
        if len(value) < 32:
            raise ValueError(
                "JWT_SECRET must be at least 32 characters. Generate one with "
                "`openssl rand -hex 32`."
            )
        return value

    @field_validator("BOOTSTRAP_ADMIN_PASSWORD")
    @classmethod
    def _reject_placeholder_admin_password(cls, value: str) -> str:
        """Refuse to boot with the .env.example placeholder admin password.

        seed.py creates this account with full admin rights on first startup.
        Shipping a deployment with the well-known "change-me" password is a
        public credential, not a secret — fail fast rather than seed it.
        """
        if value.strip().lower() in _PLACEHOLDER_SECRETS:
            raise ValueError(
                "BOOTSTRAP_ADMIN_PASSWORD is still set to the .env.example "
                "placeholder. Set a strong, unique password before first startup."
            )
        if len(value) < 12:
            raise ValueError("BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters.")
        return value

    @field_validator("NEXUS_CONFIG_ENCRYPTION_KEY")
    @classmethod
    def _reject_weak_encryption_key(cls, value: str) -> str:
        """Require a real, dedicated key for the at-rest Nexus password.

        This previously fell back to ``JWT_SECRET`` when unset "so the app
        still boots", which collapsed two secrets with different threat models
        into one: leaking JWT_SECRET then both forged sessions *and* decrypted
        the stored Nexus admin password out of a database backup. Requiring it
        explicitly is the same fail-fast treatment JWT_SECRET already gets.
        """
        if value.strip().lower() in _PLACEHOLDER_SECRETS:
            raise ValueError(
                "NEXUS_CONFIG_ENCRYPTION_KEY is still set to the .env.example "
                "placeholder. Generate a real one: `openssl rand -hex 32`."
            )
        if len(value) < 32:
            raise ValueError(
                "NEXUS_CONFIG_ENCRYPTION_KEY must be at least 32 characters. "
                "Generate one with `openssl rand -hex 32`. It must be different "
                "from JWT_SECRET so the two can be rotated independently."
            )
        return value

    @model_validator(mode="after")
    def _check_secret_separation(self) -> "Settings":
        """The at-rest key must not simply be a copy of the signing key."""
        if self.NEXUS_CONFIG_ENCRYPTION_KEY == self.JWT_SECRET:
            raise ValueError(
                "NEXUS_CONFIG_ENCRYPTION_KEY must not be the same value as "
                "JWT_SECRET — that reintroduces the single-secret weakness it "
                "exists to remove. Generate a separate `openssl rand -hex 32`."
            )
        return self

    @model_validator(mode="after")
    def _check_cookie_secure(self) -> "Settings":
        """Reject the https-origin + insecure-cookie combination.

        ``COOKIE_SECURE=false`` is legitimate for local HTTP development. It is
        never legitimate alongside an https frontend origin: that pairing ships
        auth cookies without the ``Secure`` flag to a TLS site, so any plaintext
        hop leaks them. Treat it as the misconfiguration it is.
        """
        if not self.COOKIE_SECURE:
            https_origins = [o for o in self.cors_origins if o.lower().startswith("https://")]
            if https_origins:
                raise ValueError(
                    f"COOKIE_SECURE=false with an https FRONTEND_ORIGIN "
                    f"({', '.join(https_origins)}) would send auth cookies "
                    "without the Secure flag over TLS. Set COOKIE_SECURE=true."
                )
            logger.warning(
                "COOKIE_SECURE=false — auth cookies will be sent without the "
                "Secure flag. This is only safe for local HTTP development; "
                "set COOKIE_SECURE=true for any deployment behind TLS."
            )
        return self

    @property
    def outbound_allowed_hosts(self) -> set[str]:
        """Hosts exempt from the private-address block in app/core/outbound.py."""
        return {
            h.strip().lower()
            for h in self.OUTBOUND_ALLOWED_HOSTS.split(",")
            if h.strip()
        }

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
