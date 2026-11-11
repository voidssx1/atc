from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Any, Iterator, List, Optional
from pydantic import BeforeValidator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _BASE_DIR / ".env"


def parse_database_url(v: str) -> str:
    if not v:
        return v
    # SQLAlchemy requires postgresql+psycopg:// for psycopg 3
    if v.startswith("postgres://"):
        return v.replace("postgres://", "postgresql+psycopg://", 1)
    if v.startswith("postgresql://") and not v.startswith("postgresql+"):
        return v.replace("postgresql://", "postgresql+psycopg://", 1)
    return v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_ENV_FILE, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    PROJECT_NAME: str = "ATC DSA Roadmap & Resource Hub"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"

    # Database connection string for Neon PostgreSQL
    DATABASE_URL: Annotated[str, BeforeValidator(parse_database_url)] = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/atc_hub"
    )
    DATABASE_URL_POOLED: Optional[str] = None
    HYPERDRIVE_DATABASE_URL: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def resolve_database_url(cls, data: Any) -> Any:
        if isinstance(data, dict):
            url = data.get("DATABASE_URL")
            pooled = data.get("DATABASE_URL_POOLED")
            hyperdrive = data.get("HYPERDRIVE_DATABASE_URL")
            if hyperdrive:
                data["DATABASE_URL"] = hyperdrive
            elif (not url or "localhost" in url) and pooled:
                data["DATABASE_URL"] = pooled
        return data

    # Server-Side Session Authentication
    AUTH_MODE: str = "hybrid"  # 'clerk' | 'hybrid' | 'local'
    SESSION_MAX_LIFETIME_DAYS: int = 30  # 30-day absolute maximum lifetime
    SESSION_INACTIVITY_DAYS: int = 7     # 7-day student inactivity sliding expiration
    SESSION_ADMIN_INACTIVITY_MINUTES: int = 30  # 30-minute admin inactivity timeout
    SESSION_ROTATION_GRACE_SECONDS: int = 30    # 30-second rotation overlap for concurrent tabs
    SESSION_EXPIRE_DAYS: int = 30
    SECRET_KEY: str = "development-secret-key-atc-dsa-hub-minimum-32-chars-long"
    STUDENT_ALLOWED_EMAIL_DOMAIN: str = "bennett.edu.in"

    # Clerk External Credential Provider (Free Hobby Plan)
    CLERK_SECRET_KEY: Optional[str] = None
    CLERK_WEBHOOK_SECRET: Optional[str] = None
    CLERK_JWKS_URL: Optional[str] = None
    CLERK_JWT_KEY: Optional[str] = None
    CLERK_ISSUER: Optional[str] = None
    CLERK_ALLOWED_ORIGINS: str = ""

    # CORS Origins (comma-separated)
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    def get_allowed_origins(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    def get_clerk_allowed_origins(self) -> List[str]:
        return [o.strip() for o in self.CLERK_ALLOWED_ORIGINS.split(",") if o.strip()]

    # Password Hashing Configuration (OWASP PBKDF2-HMAC-SHA256)
    PBKDF2_ITERATIONS: int = 600_000  # OWASP standard for PBKDF2-HMAC-SHA256

    # HTTPS Email Providers (Resend primary, Brevo backup)
    RESEND_API_KEY: Optional[str] = None
    BREVO_API_KEY: Optional[str] = None
    EMAILS_FROM_EMAIL: str = "noreply@bennett.edu.in"
    EMAILS_FROM_NAME: str = "ATC DSA Roadmap Hub"

    # Legacy SMTP Settings (fallback if configured)
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None

    # Cloudflare / Production Origin URL
    CLOUDFLARE_ORIGIN_URL: Optional[str] = None

    @staticmethod
    def _is_placeholder(val: Optional[str]) -> bool:
        if not val:
            return True
        lowered = val.lower()
        return "replace_with_" in lowered or "<your-" in lowered

    @model_validator(mode="after")
    def validate_production_clerk_config(self) -> "Settings":
        if (
            self.ENVIRONMENT.lower() == "production"
            and self.AUTH_MODE.lower() == "clerk"
        ):
            missing = []
            if self._is_placeholder(self.CLERK_ISSUER):
                missing.append("CLERK_ISSUER")
            if not self.get_clerk_allowed_origins() or any(
                self._is_placeholder(o) for o in self.get_clerk_allowed_origins()
            ):
                missing.append("CLERK_ALLOWED_ORIGINS")
            if self._is_placeholder(self.CLERK_SECRET_KEY):
                missing.append("CLERK_SECRET_KEY")
            if self._is_placeholder(self.CLERK_WEBHOOK_SECRET):
                missing.append("CLERK_WEBHOOK_SECRET")
            if self._is_placeholder(self.CLERK_JWKS_URL) and self._is_placeholder(
                self.CLERK_JWT_KEY
            ):
                missing.append("CLERK_JWKS_URL or CLERK_JWT_KEY")
            if missing:
                raise ValueError(
                    f"Production Clerk mode requires valid configuration for: {', '.join(missing)}"
                )
        return self


settings = Settings()

_request_settings_ctx: ContextVar[Optional[Settings]] = ContextVar(
    "request_settings", default=None
)


def get_settings() -> Settings:
    """
    Return the active Settings instance for the current async/request context.
    
    In Cloudflare Workers: returns the per-request Settings constructed from `env` bindings.
    In local dev / pytest: falls back cleanly to module-level `settings`.
    """
    return _request_settings_ctx.get() or settings


def _extract_env_value(env: Any, key: str) -> Any:
    if isinstance(env, dict):
        return env.get(key)
    try:
        val = getattr(env, key)
    except AttributeError:
        return None
    if isinstance(val, (str, int, float, bool)):
        return val
    return None


def extract_hyperdrive_url(env: Any) -> Optional[str]:
    """Safely extract Hyperdrive connection string from a Worker `env` binding."""
    if env is None:
        return None
    hyperdrive = (
        env.get("HYPERDRIVE") or env.get("DB")
        if isinstance(env, dict)
        else (getattr(env, "HYPERDRIVE", None) or getattr(env, "DB", None))
    )
    if hyperdrive is None:
        return None
    conn_str = (
        hyperdrive.get("connectionString") or hyperdrive.get("connection_string")
        if isinstance(hyperdrive, dict)
        else (
            getattr(hyperdrive, "connectionString", None)
            or getattr(hyperdrive, "connection_string", None)
        )
    )
    if isinstance(conn_str, str) and conn_str.strip():
        return conn_str.strip()
    return None


def build_settings_from_worker_env(
    env: Any, base: Optional[Settings] = None
) -> Settings:
    """
    Build an isolated Settings instance combining local defaults with Cloudflare
    Worker `env` bindings without mutating the shared global `settings` object.
    """
    source = base or settings
    merged = source.model_dump()

    for field_name in Settings.model_fields:
        val = _extract_env_value(env, field_name)
        if val is not None:
            merged[field_name] = val

    hd_url = extract_hyperdrive_url(env)
    if hd_url:
        merged["HYPERDRIVE_DATABASE_URL"] = hd_url
        merged["DATABASE_URL"] = hd_url

    return Settings(_env_file=None, **merged)


@contextmanager
def worker_settings_context(env: Any) -> Iterator[Settings]:
    """
    Context manager binding an isolated Settings instance for a Worker request or
    scheduled invocation, resetting cleanly to prevent cross-request secret leakage.
    """
    if env is None:
        yield get_settings()
        return

    req_settings = build_settings_from_worker_env(env)
    token = _request_settings_ctx.set(req_settings)
    try:
        yield req_settings
    finally:
        _request_settings_ctx.reset(token)
