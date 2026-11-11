import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from app.core.config import settings
from app.services.email import EmailService


def test_email_mock_mode_in_development():
    settings.ENVIRONMENT = "development"
    settings.RESEND_API_KEY = None
    settings.BREVO_API_KEY = None

    result = asyncio.run(
        EmailService.send_email(
            to_email="student@bennett.edu.in",
            subject="Test Mock Email",
            html_content="<p>Hello Student</p>",
        )
    )
    assert result is True


def test_email_production_failure_without_keys():
    settings.ENVIRONMENT = "production"
    settings.RESEND_API_KEY = None
    settings.BREVO_API_KEY = None

    try:
        result = asyncio.run(
            EmailService.send_email(
                to_email="student@bennett.edu.in",
                subject="Test Production Email",
                html_content="<p>Hello Student</p>",
            )
        )
        assert result is False
    finally:
        settings.ENVIRONMENT = "development"


def test_resend_primary_success():
    settings.RESEND_API_KEY = "re_test_key"
    settings.BREVO_API_KEY = "brevo_test_key"

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch.object(
        httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
    ) as mock_post:
        result = asyncio.run(
            EmailService.send_email(
                to_email="student@bennett.edu.in",
                subject="Welcome",
                html_content="<p>Welcome</p>",
            )
        )
        assert result is True
        assert mock_post.call_count == 1
        assert "api.resend.com" in mock_post.call_args[0][0]


def test_resend_server_error_falls_back_to_brevo():
    settings.RESEND_API_KEY = "re_test_key"
    settings.BREVO_API_KEY = "brevo_test_key"

    resend_resp = MagicMock()
    resend_resp.status_code = 500

    brevo_resp = MagicMock()
    brevo_resp.status_code = 201

    with patch.object(
        httpx.AsyncClient,
        "post",
        new_callable=AsyncMock,
        side_effect=[resend_resp, brevo_resp],
    ) as mock_post:
        result = asyncio.run(
            EmailService.send_email(
                to_email="student@bennett.edu.in",
                subject="Welcome",
                html_content="<p>Welcome</p>",
            )
        )
        assert result is True
        assert mock_post.call_count == 2
        assert "api.resend.com" in mock_post.call_args_list[0][0][0]
        assert "api.brevo.com" in mock_post.call_args_list[1][0][0]


def test_resend_ambiguous_read_timeout_suppresses_fallback():
    settings.RESEND_API_KEY = "re_test_key"
    settings.BREVO_API_KEY = "brevo_test_key"

    with patch.object(
        httpx.AsyncClient,
        "post",
        new_callable=AsyncMock,
        side_effect=httpx.ReadTimeout("Timed out"),
    ) as mock_post:
        result = asyncio.run(
            EmailService.send_email(
                to_email="student@bennett.edu.in",
                subject="Welcome",
                html_content="<p>Welcome</p>",
            )
        )
        # Senders must NOT fallback on ReadTimeout to prevent duplicate deliveries
        assert result is False
        assert mock_post.call_count == 1
        assert "api.resend.com" in mock_post.call_args[0][0]


def test_resend_client_error_suppresses_fallback():
    settings.RESEND_API_KEY = "re_test_key"
    settings.BREVO_API_KEY = "brevo_test_key"

    resend_resp = MagicMock()
    resend_resp.status_code = 422

    with patch.object(
        httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=resend_resp
    ) as mock_post:
        result = asyncio.run(
            EmailService.send_email(
                to_email="invalid-student@unknown.domain",
                subject="Welcome",
                html_content="<p>Welcome</p>",
            )
        )
        assert result is False
        assert mock_post.call_count == 1


@pytest.mark.parametrize("status_code", [401, 403, 429])
def test_resend_auth_domain_or_quota_error_falls_back_to_brevo(status_code):
    settings.RESEND_API_KEY = "re_test_key"
    settings.BREVO_API_KEY = "brevo_test_key"

    resend_resp = MagicMock()
    resend_resp.status_code = status_code

    brevo_resp = MagicMock()
    brevo_resp.status_code = 201

    with patch.object(
        httpx.AsyncClient,
        "post",
        new_callable=AsyncMock,
        side_effect=[resend_resp, brevo_resp],
    ) as mock_post:
        result = asyncio.run(
            EmailService.send_email(
                to_email="student@bennett.edu.in",
                subject="Welcome",
                html_content="<p>Welcome</p>",
            )
        )
        assert result is True
        assert mock_post.call_count == 2
        assert "api.resend.com" in mock_post.call_args_list[0][0][0]
        assert "api.brevo.com" in mock_post.call_args_list[1][0][0]
