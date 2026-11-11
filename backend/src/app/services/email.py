import logging
from typing import Optional, Tuple
import httpx

from app.core.config import get_settings
from app.core.http import create_async_http_client

logger = logging.getLogger("email_service")


class ResendProvider:
    """Primary email delivery provider using Resend HTTPS REST API (async for Workers)."""

    ENDPOINT = "https://api.resend.com/emails"

    @classmethod
    async def send(
        cls,
        api_key: str,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
    ) -> Tuple[bool, bool, Optional[str]]:
        """
        Asynchronously send email via Resend HTTPS API using httpx.AsyncClient.
        
        Returns:
            Tuple of (success, should_fallback, error_summary)
            - should_fallback is False on ambiguous ReadTimeout to prevent duplicate deliveries.
        """
        cfg = get_settings()
        payload = {
            "from": f"{cfg.EMAILS_FROM_NAME} <{cfg.EMAILS_FROM_EMAIL}>",
            "to": [to_email],
            "subject": subject,
            "html": html_content,
        }
        if text_content:
            payload["text"] = text_content

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with create_async_http_client(
                timeout=httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)
            ) as client:
                response = await client.post(cls.ENDPOINT, json=payload, headers=headers)

            if 200 <= response.status_code < 300:
                logger.info(f"[Resend] Email successfully delivered to {to_email}")
                return True, False, None

            # Auth, unverified-domain/policy rejection, or quota/rate limits did not send: allow fallback
            if response.status_code in (401, 403, 429):
                err_msg = f"Provider error HTTP {response.status_code}"
                logger.warning(f"[Resend] Eligible fallback failure dispatching to {to_email}: {err_msg}")
                return False, True, err_msg

            # Remaining 4xx (e.g. 400/422 payload/address error): non-retryable client error
            if 400 <= response.status_code < 500:
                err_msg = f"Client error HTTP {response.status_code}"
                logger.warning(f"[Resend] Non-retryable error dispatching to {to_email}: {err_msg}")
                return False, False, err_msg

            # Server errors (5xx): transient service error, eligible for fallback
            err_msg = f"Server error HTTP {response.status_code}"
            logger.warning(f"[Resend] Server error dispatching to {to_email}: {err_msg}")
            return False, True, err_msg

        except httpx.ConnectTimeout:
            logger.warning(f"[Resend] Connect timeout to {cls.ENDPOINT}: connection could not be established")
            return False, True, "ConnectTimeout"
        except httpx.ReadTimeout:
            # Ambiguous timeout: request may have reached Resend; do not fallback to prevent duplicate emails
            logger.warning(
                f"[Resend] Ambiguous ReadTimeout for {to_email}: response timed out. "
                "Aborting fallback to prevent duplicate email dispatch."
            )
            return False, False, "AmbiguousReadTimeout"
        except httpx.RequestError as e:
            logger.warning(f"[Resend] Network request error: {type(e).__name__}")
            return False, True, type(e).__name__
        except Exception as e:
            logger.error(f"[Resend] Unexpected error dispatching to {to_email}: {type(e).__name__}")
            return False, True, type(e).__name__


class BrevoProvider:
    """Fallback email delivery provider using Brevo HTTPS REST API (async for Workers)."""

    ENDPOINT = "https://api.brevo.com/v3/smtp/email"

    @classmethod
    async def send(
        cls,
        api_key: str,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Asynchronously send email via Brevo HTTPS API using httpx.AsyncClient."""
        cfg = get_settings()
        payload = {
            "sender": {
                "name": cfg.EMAILS_FROM_NAME,
                "email": cfg.EMAILS_FROM_EMAIL,
            },
            "to": [{"email": to_email}],
            "subject": subject,
            "htmlContent": html_content,
        }
        if text_content:
            payload["textContent"] = text_content

        headers = {
            "api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            async with create_async_http_client(timeout=httpx.Timeout(5.0)) as client:
                response = await client.post(cls.ENDPOINT, json=payload, headers=headers)

            if 200 <= response.status_code < 300:
                logger.info(f"[Brevo Fallback] Email successfully delivered to {to_email}")
                return True, None

            err_msg = f"HTTP {response.status_code}"
            logger.warning(f"[Brevo Fallback] Failed dispatching to {to_email}: {err_msg}")
            return False, err_msg

        except Exception as e:
            logger.error(f"[Brevo Fallback] Request error dispatching to {to_email}: {type(e).__name__}")
            return False, type(e).__name__


class EmailService:
    """
    High-level async email dispatch service compatible with Cloudflare Python Workers.
    """

    @classmethod
    async def send_email(
        cls,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
    ) -> bool:
        cfg = get_settings()
        resend_key = cfg.RESEND_API_KEY
        brevo_key = cfg.BREVO_API_KEY

        # 1. Try Resend if configured
        if resend_key:
            success, should_fallback, err = await ResendProvider.send(
                api_key=resend_key,
                to_email=to_email,
                subject=subject,
                html_content=html_content,
                text_content=text_content,
            )
            if success:
                return True

            if not should_fallback:
                logger.error(
                    f"Email dispatch to {to_email} stopped after Resend failure (fallback suppressed: {err})"
                )
                return False

            logger.info(f"Attempting fallback to Brevo for recipient {to_email}...")

        # 2. Try Brevo fallback if configured
        if brevo_key:
            success, err = await BrevoProvider.send(
                api_key=brevo_key,
                to_email=to_email,
                subject=subject,
                html_content=html_content,
                text_content=text_content,
            )
            if success:
                return True

            logger.error(f"Brevo delivery also failed for recipient {to_email}: {err}")
            return False

        # 3. If neither provider is configured:
        if cfg.ENVIRONMENT.lower() == "production":
            logger.error(
                f"[PRODUCTION ERROR] Email dispatch to {to_email} aborted: "
                "Neither RESEND_API_KEY nor BREVO_API_KEY is configured in production environment."
            )
            return False

        # Mock delivery in development / test environments
        logger.info(
            f"[MOCK EMAIL DISPATCHED] To: {to_email} | Subject: {subject} | "
            f"Content snippet: {(text_content or html_content)[:80]}..."
        )
        return True

    @classmethod
    async def send_verification_email(cls, to_email: str, token: str) -> bool:
        subject = "Verify your ATC DSA Roadmap account"
        verification_link = f"/verify-email?token={token}"
        html = f"""
        <h2>Welcome to ATC DSA Roadmap & Resource Hub!</h2>
        <p>Please verify your email address by clicking the link below:</p>
        <p><a href="{verification_link}">Verify My Email</a></p>
        <p>Or use your verification token: <strong>{token}</strong></p>
        """
        text = f"Welcome to ATC DSA Hub. Verification token: {token}"
        return await cls.send_email(to_email, subject, html, text)

    @classmethod
    async def send_password_reset_email(cls, to_email: str, token: str) -> bool:
        subject = "Reset your ATC DSA Roadmap password"
        reset_link = f"/reset-password?token={token}"
        html = f"""
        <h2>Password Reset Request</h2>
        <p>You requested to reset your password. Use the link below to set a new password:</p>
        <p><a href="{reset_link}">Reset Password</a></p>
        <p>Or use your reset token: <strong>{token}</strong></p>
        <p>If you did not make this request, you can safely ignore this email.</p>
        """
        text = f"Password reset token: {token}"
        return await cls.send_email(to_email, subject, html, text)


email_service = EmailService()
