from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_user,
    get_db,
    get_inactivity_delta,
    oauth2_scheme,
)
from app.core.config import get_settings
from app.core.security import (
    _HAS_WEB_CRYPTO,
    generate_random_token,
    generate_session_token,
    hash_password,
    hash_session_token,
    is_allowed_student_email,
    is_legacy_bcrypt_hash,
    verify_password,
)
from app.models.analytics import AuditLog
from app.models.clerk import ClerkOutbox
from app.models.user import StudentProfile, User, UserRole, UserSession
from app.schemas.user import (
    ClerkExchangeRequest,
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
    SessionTokenRequest,
    StudentRegisterRequest,
    Token,
    UserResponse,
    VerifyEmailRequest,
)
from app.services.audit import log_audit_event
from app.services.clerk import (
    ClerkAPIError,
    ClerkJWTVerificationError,
    ClerkOutboxService,
    fetch_clerk_user,
    verify_clerk_jwt,
)
from app.services.email import email_service


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_expired(dt: Optional[datetime], now: Optional[datetime] = None) -> bool:
    if dt is None:
        return False
    ref = now or datetime.now(timezone.utc)
    return _ensure_utc(dt) < ref


def _hard_revoke_sessions(
    db: Session,
    user_id,
    clerk_session_id: Optional[str] = None,
    exclude_clerk_session_id: Optional[str] = None,
) -> int:
    """Immediately hard-revoke matching sessions with zero grace window."""
    query = db.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.is_revoked == False,  # noqa: E712
    )
    if clerk_session_id is not None:
        query = query.filter(UserSession.clerk_session_id == clerk_session_id)
    if exclude_clerk_session_id is not None:
        query = query.filter(
            (UserSession.clerk_session_id != exclude_clerk_session_id)
            | (UserSession.clerk_session_id.is_(None))
        )
    return query.update(
        {
            "is_revoked": True,
            "grace_until": None,
            "enforce_single_session": False,
        },
        synchronize_session=False,
    )


def _issue_application_session(
    db: Session,
    user: User,
    request: Request,
    clerk_session_id: Optional[str] = None,
) -> Token:
    """
    Create a database-backed opaque session token stored as SHA-256 hash.

    Enforces:
      - Row-level lock on User row to serialize concurrent logins
      - TECH_ADMIN single active session (hard-revokes prior sessions from other devices/sids)
      - 30-second rotation grace window when re-exchanging the same Clerk sid
      - Student 30-day max / 7-day inactivity & Admin 30-minute inactivity
      - Exact clamped expires_in
    """
    # Serialize concurrent login/exchange attempts for the same user
    user_query = db.query(User).filter(User.id == user.id)
    if db.bind and db.bind.dialect.name == "postgresql":
        user_query = user_query.with_for_update()
    user_query.first()

    cfg = get_settings()
    now = datetime.now(timezone.utc)

    # TECH_ADMIN single active session restriction across different devices/logins
    if user.role == UserRole.TECH_ADMIN:
        _hard_revoke_sessions(
            db,
            user_id=user.id,
            exclude_clerk_session_id=clerk_session_id,
        )
        db.flush()

    anchor_created_at = now

    # Handle rotation on repeated/concurrent exchange for the SAME Clerk sid
    if clerk_session_id:
        prev_primary = (
            db.query(UserSession)
            .filter(
                UserSession.user_id == user.id,
                UserSession.clerk_session_id == clerk_session_id,
                UserSession.is_revoked == False,  # noqa: E712
                UserSession.grace_until.is_(None),
            )
            .first()
        )
        if prev_primary:
            prev_created = _ensure_utc(prev_primary.created_at)
            prev_max_lifetime = prev_created + timedelta(
                days=cfg.SESSION_MAX_LIFETIME_DAYS
            )
            if _is_expired(prev_max_lifetime, now) or _is_expired(prev_primary.expires_at, now):
                # Previous primary session already expired; hard-revoke cleanly
                prev_primary.is_revoked = True
                prev_primary.grace_until = None
                prev_primary.enforce_single_session = False
            else:
                # Transition active previous token into 30-second rotation grace window
                anchor_created_at = prev_created
                prev_primary.grace_until = now + timedelta(
                    seconds=cfg.SESSION_ROTATION_GRACE_SECONDS
                )
                prev_primary.enforce_single_session = False
            db.flush()

    max_lifetime_expires_at = anchor_created_at + timedelta(
        days=cfg.SESSION_MAX_LIFETIME_DAYS
    )
    inactivity_delta = get_inactivity_delta(user.role)
    effective_expires_at = min(now + inactivity_delta, max_lifetime_expires_at)
    expires_in = max(0, int((effective_expires_at - now).total_seconds()))

    raw_token = generate_session_token(48)
    token_hash = hash_session_token(raw_token)

    new_session = UserSession(
        user_id=user.id,
        token=token_hash,
        clerk_session_id=clerk_session_id,
        grace_until=None,
        enforce_single_session=(user.role == UserRole.TECH_ADMIN),
        expires_at=effective_expires_at,
        is_revoked=False,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
        created_at=anchor_created_at,
    )
    db.add(new_session)
    db.commit()

    return Token(
        session_token=raw_token,
        access_token=raw_token,
        token_type="bearer",
        expires_in=expires_in,
    )


router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/clerk/exchange", response_model=Token)
async def exchange_clerk_token(
    request: Request,
    payload: Optional[ClerkExchangeRequest] = None,
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """
    Exchange a verified Clerk session JWT for a Neon-backed opaque application session.
    """
    raw_clerk_jwt: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        raw_clerk_jwt = authorization.split(" ", 1)[1].strip()
    elif payload and payload.token:
        raw_clerk_jwt = payload.token.strip()

    if not raw_clerk_jwt:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clerk session token is required",
        )

    try:
        claims = await verify_clerk_jwt(raw_clerk_jwt)
    except ClerkJWTVerificationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

    clerk_sub = claims["sub"]
    clerk_sid = claims["sid"]

    try:
        identity = await fetch_clerk_user(clerk_sub)
    except ClerkAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

    # Reconciliation: immediately deactivate local user & revoke sessions if banned/locked in Clerk
    existing_linked = (
        db.query(User).filter(User.clerk_user_id == identity.clerk_user_id).first()
    )
    if identity.banned or identity.locked:
        if existing_linked:
            existing_linked.is_active = False
            _hard_revoke_sessions(db, user_id=existing_linked.id)
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is banned or locked",
        )

    if not identity.email or not identity.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A verified email address is required",
        )

    user: Optional[User] = existing_linked

    if user:
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is deactivated. Please contact support.",
            )
        # Reconcile verified email changes safely
        if user.email != identity.email:
            if user.role == UserRole.STUDENT and not is_allowed_student_email(identity.email):
                user.is_active = False
                _hard_revoke_sessions(db, user_id=user.id)
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Student accounts must maintain a verified @bennett.edu.in email",
                )
            conflict = db.query(User).filter(User.email == identity.email, User.id != user.id).first()
            if conflict:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Updated email address is already associated with another account",
                )
            user.email = identity.email
        user.is_verified = True
        if identity.updated_at is not None:
            user.clerk_updated_at = identity.updated_at
        db.flush()
    else:
        # Check for unlinked pre-provisioned local account matching verified email
        email_query = db.query(User).filter(User.email == identity.email)
        if db.bind and db.bind.dialect.name == "postgresql":
            email_query = email_query.with_for_update()
        user_by_email = email_query.first()

        if user_by_email:
            if user_by_email.clerk_user_id is not None:
                if user_by_email.clerk_user_id == identity.clerk_user_id:
                    if not user_by_email.is_active:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="Account is deactivated. Please contact support.",
                        )
                    user = user_by_email
                else:
                    log_audit_event(
                        db=db,
                        action="clerk_link_conflict",
                        user_id=user_by_email.id,
                        target_type="user",
                        target_id=str(user_by_email.id),
                        ip_address=request.client.host if request.client else None,
                    )
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Account is already linked to a different authentication identity",
                    )
            else:
                if not user_by_email.is_active:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Account is deactivated. Please contact support.",
                    )
                try:
                    with db.begin_nested():
                        user_by_email.clerk_user_id = identity.clerk_user_id
                        user_by_email.clerk_updated_at = identity.updated_at
                        user_by_email.is_verified = True
                        db.flush()
                    user = user_by_email
                    log_audit_event(
                        db=db,
                        action="clerk_account_linked",
                        user_id=user.id,
                        target_type="user",
                        target_id=str(user.id),
                        details={"role": user.role.value},
                        ip_address=request.client.host if request.client else None,
                    )
                except IntegrityError:
                    # Handle concurrent link collision via re-query
                    reloaded = (
                        db.query(User)
                        .filter(User.clerk_user_id == identity.clerk_user_id)
                        .first()
                    )
                    if reloaded:
                        if not reloaded.is_active:
                            raise HTTPException(
                                status_code=status.HTTP_403_FORBIDDEN,
                                detail="Account is deactivated. Please contact support.",
                            )
                        user = reloaded
                    else:
                        reloaded_email = (
                            db.query(User)
                            .filter(User.email == identity.email)
                            .first()
                        )
                        if reloaded_email and reloaded_email.clerk_user_id == identity.clerk_user_id:
                            if not reloaded_email.is_active:
                                raise HTTPException(
                                    status_code=status.HTTP_403_FORBIDDEN,
                                    detail="Account is deactivated. Please contact support.",
                                )
                            user = reloaded_email
                        else:
                            raise HTTPException(
                                status_code=status.HTTP_409_CONFLICT,
                                detail="Account is already linked to a different authentication identity",
                            )
        else:
            # New public registration -> strictly @bennett.edu.in & STUDENT role only
            if not is_allowed_student_email(identity.email):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Registration is restricted strictly to @bennett.edu.in email addresses",
                )

            base_username = identity.email.split("@")[0]
            username_clean = base_username
            if db.query(User).filter(User.username == username_clean).first():
                username_clean = f"{base_username}_{generate_random_token(4)}"

            try:
                with db.begin_nested():
                    user = User(
                        email=identity.email,
                        username=username_clean,
                        clerk_user_id=identity.clerk_user_id,
                        clerk_updated_at=identity.updated_at,
                        hashed_password=None,
                        role=UserRole.STUDENT,
                        is_active=True,
                        is_verified=True,
                    )
                    db.add(user)
                    db.flush()

                    profile = StudentProfile(
                        user_id=user.id,
                        full_name=identity.full_name or username_clean,
                    )
                    db.add(profile)
                    db.flush()

                log_audit_event(
                    db=db,
                    action="user_registered",
                    user_id=user.id,
                    target_type="user",
                    target_id=str(user.id),
                    details={"role": user.role.value, "provider": "clerk"},
                    ip_address=request.client.host if request.client else None,
                )
            except IntegrityError:
                # Concurrent first-login request already created this account
                user = (
                    db.query(User)
                    .filter(User.clerk_user_id == identity.clerk_user_id)
                    .first()
                )
                if not user:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Account registration conflict; please retry login.",
                    )
                if not user.is_active:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Account is deactivated. Please contact support.",
                    )

    token_response = _issue_application_session(
        db=db,
        user=user,
        request=request,
        clerk_session_id=clerk_sid,
    )

    log_audit_event(
        db=db,
        action="user_login",
        user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        details={"provider": "clerk"},
        ip_address=request.client.host if request.client else None,
    )
    return token_response


@router.post("/sessions/revoke-all")
def revoke_all_user_sessions(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Hard-revoke all active opaque sessions for the current user.
    Called after Clerk password reset/change flows.
    """
    revoked_count = _hard_revoke_sessions(db, user_id=current_user.id)
    db.commit()

    log_audit_event(
        db=db,
        action="sessions_revoked_all",
        user_id=current_user.id,
        target_type="user",
        target_id=str(current_user.id),
        details={"revoked_count": revoked_count},
        ip_address=request.client.host if request.client else None,
    )
    return {"message": "All sessions revoked", "revoked_count": revoked_count}


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register_student(
    payload: StudentRegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    if get_settings().AUTH_MODE.lower() == "clerk":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Direct password registration is disabled; please sign up via Clerk.",
        )

    email_clean = payload.email.lower().strip()
    if not is_allowed_student_email(email_clean):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Student registration is restricted strictly to @bennett.edu.in email addresses",
        )

    # Check if email exists
    existing_user = db.query(User).filter(User.email == email_clean).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists",
        )

    # Check username if provided
    if payload.username:
        username_clean = payload.username.strip()
        existing_username = (
            db.query(User).filter(User.username == username_clean).first()
        )
        if existing_username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username is already taken",
            )
    else:
        username_clean = email_clean.split("@")[0]

    verification_token = generate_random_token(32)
    verification_expires = datetime.now(timezone.utc) + timedelta(hours=24)

    hashed_pw = await hash_password(payload.password)
    user = User(
        email=email_clean,
        username=username_clean,
        hashed_password=hashed_pw,
        role=UserRole.STUDENT,
        is_active=True,
        is_verified=False,
        verification_token=verification_token,
        verification_token_expires_at=verification_expires,
    )
    db.add(user)
    db.flush()

    # Create associated student profile
    profile = StudentProfile(
        user_id=user.id,
        full_name=payload.full_name or username_clean,
    )
    db.add(profile)
    db.commit()
    db.refresh(user)

    # Send verification email
    await email_service.send_verification_email(user.email, verification_token)

    # Audit log
    log_audit_event(
        db=db,
        action="user_registered",
        user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        details={"email": user.email, "role": user.role.value},
        ip_address=request.client.host if request.client else None,
    )

    return user


@router.post("/verify-email")
def verify_email(payload: VerifyEmailRequest, db: Session = Depends(get_db)):
    if get_settings().AUTH_MODE.lower() == "clerk":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Direct email verification is disabled; please verify via Clerk.",
        )
    user = (
        db.query(User)
        .filter(User.verification_token == payload.token)
        .first()
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification token",
        )

    if _is_expired(user.verification_token_expires_at):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token has expired",
        )

    user.is_verified = True
    user.verification_token = None
    user.verification_token_expires_at = None
    db.commit()

    return {"message": "Email verified successfully"}


@router.post("/login", response_model=Token)
async def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    if get_settings().AUTH_MODE.lower() == "clerk":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Direct password login is disabled; please sign in via Clerk.",
        )

    email_clean = payload.email.lower().strip()
    user = db.query(User).filter(User.email == email_clean).first()

    if user and not user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account uses external Clerk authentication. Please sign in via Clerk.",
        )

    if user and is_legacy_bcrypt_hash(user.hashed_password):
        if get_settings().ENVIRONMENT.lower() == "production" or _HAS_WEB_CRYPTO:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Security upgrade required: your account uses a legacy credential format. "
                    "Please use /api/v1/auth/forgot-password to reset your password."
                ),
            )

    if not user or not (await verify_password(payload.password, user.hashed_password)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Please contact support.",
        )

    token_response = _issue_application_session(db=db, user=user, request=request)

    log_audit_event(
        db=db,
        action="user_login",
        user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        ip_address=request.client.host if request.client else None,
    )

    return token_response


@router.post("/refresh", response_model=Token)
def refresh_session(payload: SessionTokenRequest, db: Session = Depends(get_db)):
    try:
        token_str = payload.get_token()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    token_hash = hash_session_token(token_str)
    session = (
        db.query(UserSession)
        .filter(UserSession.token == token_hash)
        .first()
    )
    if not session or session.is_revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session revoked or not found",
        )

    now = datetime.now(timezone.utc)
    if session.grace_until is not None:
        if _is_expired(session.grace_until, now):
            session.is_revoked = True
            session.grace_until = None
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Rotated session token cannot be refreshed; use the latest active session token.",
        )

    user = session.user
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User inactive or not found",
        )

    # 1. Check 30-day maximum lifetime
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
        )

    # 2. Check role-based inactivity expiry
    if _is_expired(session.expires_at, now):
        session.is_revoked = True
        session.grace_until = None
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired due to inactivity. Please login again.",
        )

    # Roll hashed session token in place and compute exact clamped expires_in
    new_raw_token = generate_session_token(48)
    session.token = hash_session_token(new_raw_token)
    inactivity_delta = get_inactivity_delta(user.role)
    effective_expires_at = min(now + inactivity_delta, max_lifetime_expiry)
    session.expires_at = effective_expires_at
    expires_in = max(0, int((effective_expires_at - now).total_seconds()))
    db.commit()

    return Token(
        session_token=new_raw_token,
        access_token=new_raw_token,
        token_type="bearer",
        expires_in=expires_in,
    )


@router.post("/logout")
def logout(
    payload: Optional[SessionTokenRequest] = None,
    bearer_token: Optional[str] = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_raw_token = None
    if payload and (payload.session_token or payload.refresh_token):
        target_raw_token = payload.get_token()
    elif bearer_token:
        target_raw_token = bearer_token

    if target_raw_token:
        token_hash = hash_session_token(target_raw_token)
        session = (
            db.query(UserSession)
            .filter(
                UserSession.token == token_hash,
                UserSession.user_id == current_user.id,
            )
            .first()
        )
        if session:
            # Hard-revoke this session and any rotated siblings sharing the same clerk_session_id
            if session.clerk_session_id:
                _hard_revoke_sessions(
                    db,
                    user_id=current_user.id,
                    clerk_session_id=session.clerk_session_id,
                )
            else:
                session.is_revoked = True
                session.grace_until = None
                session.enforce_single_session = False
            db.commit()
    else:
        _hard_revoke_sessions(db, user_id=current_user.id)
        db.commit()

    return {"message": "Logged out successfully"}


@router.post("/forgot-password")
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    if get_settings().AUTH_MODE.lower() == "clerk":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Direct password reset is disabled; please reset your password via Clerk.",
        )

    user = (
        db.query(User)
        .filter(User.email == payload.email.lower().strip())
        .first()
    )
    # In hybrid mode, do not generate local password reset tokens for Clerk-linked accounts
    if user and user.clerk_user_id is None:
        reset_token = generate_random_token(32)
        user.reset_password_token = reset_token
        user.reset_password_expires_at = datetime.now(timezone.utc) + timedelta(
            hours=2
        )
        db.commit()
        await email_service.send_password_reset_email(user.email, reset_token)

    return {"message": "If this email is registered, a password reset link has been sent"}


@router.post("/reset-password")
async def reset_password(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    if get_settings().AUTH_MODE.lower() == "clerk":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Direct password reset is disabled; please reset your password via Clerk.",
        )

    user = (
        db.query(User)
        .filter(User.reset_password_token == payload.token)
        .first()
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset password token",
        )

    if user.clerk_user_id is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account uses external Clerk authentication. Please reset your password via Clerk.",
        )

    if _is_expired(user.reset_password_expires_at):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset password token has expired",
        )

    user.hashed_password = await hash_password(payload.new_password)
    user.reset_password_token = None
    user.reset_password_expires_at = None

    # Hard-revoke all active sessions upon password reset
    _hard_revoke_sessions(db, user_id=user.id)
    db.commit()

    return {"message": "Password has been reset successfully"}


@router.delete("/account", status_code=status.HTTP_200_OK)
async def delete_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = current_user.id
    clerk_user_id = current_user.clerk_user_id

    # Permanent local deletion (CASCADE deletes profile, sessions, progress, bookmarks;
    # sets user_id=NULL on AuditLog & ResourceAnalytics)
    db.delete(current_user)

    # Privacy-safe anonymized audit retention (no PII stored)
    db.add(
        AuditLog(
            user_id=None,
            action="account_deleted",
            target_type="user",
            target_id=str(user_id),
            details={"had_clerk_link": bool(clerk_user_id)},
        )
    )

    outbox_item: Optional[ClerkOutbox] = None
    if clerk_user_id:
        outbox_item = ClerkOutbox(
            clerk_user_id=clerk_user_id,
            audit_target_id=str(user_id),
            operation="delete_user",
            status="pending",
            attempts=0,
            max_attempts=5,
            next_retry_at=datetime.now(timezone.utc),
        )
        db.add(outbox_item)

    db.commit()

    # Best-effort immediate external Clerk deletion; outbox retries on transient failure
    if outbox_item is not None:
        db.refresh(outbox_item)
        await ClerkOutboxService.dispatch_item(db, outbox_item)

    return {"message": "Account deleted successfully", "user_id": str(user_id)}
