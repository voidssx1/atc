import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.v1.auth import _hard_revoke_sessions
from app.core.security import is_allowed_student_email
from app.models.analytics import AuditLog
from app.models.clerk import WebhookEvent
from app.models.user import User, UserRole, UserSession
from app.services.clerk import (
    WebhookVerificationError,
    parse_clerk_user_payload,
    verify_svix_webhook,
)

logger = logging.getLogger("clerk_webhooks")

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/clerk", status_code=status.HTTP_200_OK)
async def handle_clerk_webhook(
    request: Request,
    svix_id: Optional[str] = Header(default=None, alias="svix-id"),
    svix_timestamp: Optional[str] = Header(default=None, alias="svix-timestamp"),
    svix_signature: Optional[str] = Header(default=None, alias="svix-signature"),
    db: Session = Depends(get_db),
):
    """
    Svix-verified webhook receiver for official Clerk Webhook Event Catalog events:
      - user.updated
      - user.deleted
      - session.revoked
      - session.ended (non-revoking per non-inferential policy)
      - session.removed (non-revoking per non-inferential policy)
    """
    raw_body = await request.body()

    try:
        ts = verify_svix_webhook(
            raw_body=raw_body,
            svix_id=svix_id,
            svix_timestamp=svix_timestamp,
            svix_signature=svix_signature,
        )
    except WebhookVerificationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

    try:
        event = json.loads(raw_body.decode("utf-8"))
        event_type = event.get("type")
        data = event.get("data") or {}
        if not event_type or not isinstance(data, dict):
            raise ValueError("Invalid event structure")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed webhook JSON payload",
        )

    # Atomic idempotency check via savepoint: insert inside the same DB transaction
    db_event = WebhookEvent(
        svix_id=svix_id,
        event_type=event_type,
        svix_timestamp=ts,
    )
    try:
        with db.begin_nested():
            db.add(db_event)
            db.flush()
    except IntegrityError:
        return {"status": "duplicate_ignored", "svix_id": svix_id}

    try:
        if event_type == "user.updated":
            clerk_user_id = data.get("id")
            if clerk_user_id:
                user = (
                    db.query(User)
                    .filter(User.clerk_user_id == clerk_user_id)
                    .first()
                )
                if user:
                    incoming_updated = data.get("updated_at")
                    # Out-of-order guard: apply only newer or equal updates
                    if (
                        user.clerk_updated_at is None
                        or incoming_updated is None
                        or incoming_updated >= user.clerk_updated_at
                    ):
                        identity = parse_clerk_user_payload(data)
                        if identity.banned or identity.locked:
                            user.is_active = False
                            _hard_revoke_sessions(db, user_id=user.id)
                        else:
                            if identity.email:
                                if not identity.email_verified:
                                    user.is_verified = False
                                    _hard_revoke_sessions(db, user_id=user.id)
                                elif user.email != identity.email:
                                    if (
                                        user.role == UserRole.STUDENT
                                        and not is_allowed_student_email(identity.email)
                                    ):
                                        user.is_active = False
                                        _hard_revoke_sessions(db, user_id=user.id)
                                    else:
                                        conflict = (
                                            db.query(User)
                                            .filter(
                                                User.email == identity.email,
                                                User.id != user.id,
                                            )
                                            .first()
                                        )
                                        if not conflict:
                                            user.email = identity.email
                                            user.is_verified = True
                                        else:
                                            # Email sync collision: record sanitized audit log & revoke active sessions
                                            _hard_revoke_sessions(db, user_id=user.id)
                                            db.add(
                                                AuditLog(
                                                    user_id=user.id,
                                                    action="clerk_email_sync_conflict",
                                                    target_type="user",
                                                    target_id=str(user.id),
                                                    details={"conflict_detected": True},
                                                )
                                            )
                                else:
                                    user.is_verified = True

                        if incoming_updated is not None:
                            user.clerk_updated_at = incoming_updated

        elif event_type == "user.deleted":
            clerk_user_id = data.get("id")
            if clerk_user_id:
                user = (
                    db.query(User)
                    .filter(User.clerk_user_id == clerk_user_id)
                    .first()
                )
                if user:
                    user.is_active = False
                    user.clerk_user_id = None
                    _hard_revoke_sessions(db, user_id=user.id)

        elif event_type == "session.revoked":
            clerk_sid = data.get("id")
            if clerk_sid:
                db.query(UserSession).filter(
                    UserSession.clerk_session_id == clerk_sid,
                    UserSession.is_revoked == False,  # noqa: E712
                ).update(
                    {
                        "is_revoked": True,
                        "grace_until": None,
                        "enforce_single_session": False,
                    },
                    synchronize_session=False,
                )

        elif event_type in ("session.ended", "session.removed"):
            # Non-inferential safe policy:
            # Clerk's natural 7-day expiration triggers session.ended / session.removed;
            # explicit sign-out is revoked synchronously via POST /api/v1/auth/logout.
            pass

        db.commit()
        return {"status": "processed", "event_type": event_type}

    except Exception as e:
        db.rollback()
        logger.error(f"Webhook processing failure for {event_type}: {type(e).__name__}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Temporary failure processing webhook",
        )
