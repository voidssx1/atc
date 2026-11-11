from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_session_token
from app.db.session import get_db
from app.models.user import User, UserRole, UserSession

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    auto_error=False,
)

ADMIN_ROLES = (UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN, UserRole.VIEWER)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_expired(dt: Optional[datetime], now: Optional[datetime] = None) -> bool:
    if dt is None:
        return False
    ref = now or datetime.now(timezone.utc)
    return _ensure_utc(dt) < ref


def get_inactivity_delta(role: UserRole) -> timedelta:
    """Return role-specific inactivity timeout per approved PRD."""
    cfg = get_settings()
    if role in ADMIN_ROLES:
        return timedelta(minutes=cfg.SESSION_ADMIN_INACTIVITY_MINUTES)
    return timedelta(days=cfg.SESSION_INACTIVITY_DAYS)


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_hash = hash_session_token(token)
    session = (
        db.query(UserSession)
        .filter(UserSession.token == token_hash)
        .first()
    )
    if not session or session.is_revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked session token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    now = datetime.now(timezone.utc)

    # 1. Enforce strict 30-second rotation grace window if rotated
    if session.grace_until is not None and _is_expired(session.grace_until, now):
        session.is_revoked = True
        session.grace_until = None
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token rotation grace window has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = session.user
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    # 2. Enforce 30-day absolute maximum lifetime
    created_at = _ensure_utc(session.created_at)
    max_lifetime_expiry = created_at + timedelta(
        days=get_settings().SESSION_MAX_LIFETIME_DAYS
    )
    if _is_expired(max_lifetime_expiry, now):
        session.is_revoked = True
        session.grace_until = None
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session reached its 30-day maximum lifetime. Please login again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Enforce role-based inactivity expiry (30 mins for Admins, 7 days for Students)
    if _is_expired(session.expires_at, now):
        session.is_revoked = True
        session.grace_until = None
        db.commit()
        inactivity_msg = (
            "Admin session expired due to 30 minutes of inactivity. Please login again."
            if user.role in ADMIN_ROLES
            else "Session expired due to 7 days of inactivity. Please login again."
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=inactivity_msg,
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 4. Slide inactivity window without exceeding 30-day max lifetime (only for primary non-grace sessions)
    # Debounce writes on rapid read bursts to avoid DB commit overhead on every GET request
    if session.grace_until is None:
        inactivity_delta = get_inactivity_delta(user.role)
        target_expiry = min(now + inactivity_delta, max_lifetime_expiry)
        current_expiry = _ensure_utc(session.expires_at)
        slide_threshold = (
            timedelta(seconds=60)
            if user.role in ADMIN_ROLES
            else timedelta(minutes=15)
        )
        if (target_expiry - current_expiry) >= slide_threshold:
            session.expires_at = target_expiry
            db.commit()

    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    return current_user


def get_optional_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    if not token:
        return None
    token_hash = hash_session_token(token)
    session = (
        db.query(UserSession)
        .filter(UserSession.token == token_hash)
        .first()
    )
    if not session or session.is_revoked:
        return None

    now = datetime.now(timezone.utc)
    if session.grace_until is not None and _is_expired(session.grace_until, now):
        return None

    created_at = _ensure_utc(session.created_at)
    max_lifetime_expiry = created_at + timedelta(
        days=get_settings().SESSION_MAX_LIFETIME_DAYS
    )
    if _is_expired(max_lifetime_expiry, now) or _is_expired(session.expires_at, now):
        return None

    user = session.user
    return user if user and user.is_active else None


def require_role(allowed_roles: List[UserRole]):
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Action requires one of roles: {[r.value for r in allowed_roles]}",
            )
        return current_user

    return role_checker


def ensure_admin_for_flag(
    enabled: bool,
    current_user: Optional[User],
    detail: str,
) -> None:
    """Enforce authentication and admin RBAC when privileged query flags are enabled."""
    if not enabled:
        return
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if current_user.role not in ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )
