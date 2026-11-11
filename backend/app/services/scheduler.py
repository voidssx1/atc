import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from app.models.resource import Resource, AvailabilityStatus
from app.models.user import UserSession
from app.services.clerk import ClerkOutboxService
from app.services.url_checker import check_url_availability

logger = logging.getLogger("scheduled_jobs")

# Prevent overlapping scheduled job execution inside the same Worker isolate
_scheduled_lock = asyncio.Lock()


class JobRunnerService:
    @staticmethod
    async def run_resource_availability_scan(db: Session, limit: int = 50) -> dict:
        resources = (
            db.query(Resource)
            .filter(Resource.is_archived == False)  # noqa: E712
            .order_by(Resource.last_checked_at.asc().nullsfirst())
            .limit(limit)
            .all()
        )
        checked = 0
        broken = 0
        degraded = 0
        available = 0

        for res in resources:
            status, code = await check_url_availability(res.url)
            res.availability_status = status
            res.status_code = code
            res.last_checked_at = datetime.now(timezone.utc)
            checked += 1
            if status == AvailabilityStatus.BROKEN:
                broken += 1
            elif status == AvailabilityStatus.DEGRADED:
                degraded += 1
            elif status == AvailabilityStatus.AVAILABLE:
                available += 1

        db.commit()
        return {
            "total_checked": checked,
            "available": available,
            "broken": broken,
            "degraded": degraded,
        }

    @staticmethod
    def cleanup_expired_sessions(db: Session) -> int:
        now = datetime.now(timezone.utc)
        deleted = (
            db.query(UserSession)
            .filter(
                (UserSession.expires_at < now)
                | (
                    UserSession.grace_until.isnot(None)
                    & (UserSession.grace_until < now)
                )
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        return deleted

    @classmethod
    async def run_scheduled_batch(
        cls, db: Session, cron_expr: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute scheduled maintenance tasks safely with overlap guard and isolated
        error handling per job (no secrets or PII logged).
        
        Schedule behavior:
          - Hourly ('0 1-23 * * *'): session cleanup + Clerk outbox retries
          - Daily midnight ('0 0 * * *') or unspecified: all 3 jobs including availability scan
        """
        if _scheduled_lock.locked():
            logger.info("Scheduled execution skipped: prior run still active in isolate")
            return {"skipped": True, "reason": "overlapping_execution"}

        results: Dict[str, Any] = {
            "skipped": False,
            "cron": cron_expr,
            "sessions_cleaned": None,
            "clerk_outbox": None,
            "availability_scan": None,
            "errors": [],
        }

        async with _scheduled_lock:
            # 1. Expired & rotated-grace session cleanup
            try:
                results["sessions_cleaned"] = cls.cleanup_expired_sessions(db)
            except Exception as e:
                db.rollback()
                logger.error(f"Scheduled job 'cleanup_expired_sessions' failed: {type(e).__name__}")
                results["errors"].append(f"cleanup_expired_sessions:{type(e).__name__}")

            # 2. Clerk deletion outbox retries & completed purge
            try:
                results["clerk_outbox"] = await ClerkOutboxService.process_pending(db, limit=25)
            except Exception as e:
                db.rollback()
                logger.error(f"Scheduled job 'clerk_outbox' failed: {type(e).__name__}")
                results["errors"].append(f"clerk_outbox:{type(e).__name__}")

            # 3. Daily resource URL availability scan
            if cron_expr is None or cron_expr == "0 0 * * *":
                try:
                    results["availability_scan"] = await cls.run_resource_availability_scan(
                        db, limit=20
                    )
                except Exception as e:
                    db.rollback()
                    logger.error(f"Scheduled job 'availability_scan' failed: {type(e).__name__}")
                    results["errors"].append(f"availability_scan:{type(e).__name__}")

        return results


job_runner = JobRunnerService()
