from unittest.mock import AsyncMock, MagicMock, patch

import aiosmtplib
import pytest

from app.services.email.smtp import EmailSendError, SmtpSendConfig, send_report_email


def _cfg(**over):
    base = dict(host="smtp.x.io", port=587, security="starttls", username="u",
                password="p", from_email="noc@x.io", from_name="OPNGMS NOC")
    base.update(over)
    return SmtpSendConfig(**base)


def _mock_client(captured: dict):
    """A mock aiosmtplib.SMTP client that records the constructor kwargs, the sent message and
    whether login was called. Patches in via the constructor's captured kwargs."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.auth_xoauth2 = AsyncMock()
    client.login = AsyncMock()

    async def fake_send_message(message):
        captured["message"] = message
        return ({}, "ok")

    client.send_message = AsyncMock(side_effect=fake_send_message)
    return client


async def test_send_builds_message_with_pdf_attachment():
    captured: dict = {}
    client = _mock_client(captured)
    with patch("app.services.email.smtp.aiosmtplib.SMTP", return_value=client) as ctor:
        await send_report_email(
            _cfg(),
            subject="Weekly report",
            recipients=["a@x.io", "b@x.io"],
            body_text="Attached.",
            attachment=("report.pdf", b"%PDF-1.4 fake", "application/pdf"),
        )
    msg = captured["message"]
    assert msg["Subject"] == "Weekly report"
    assert msg["From"] == "OPNGMS NOC <noc@x.io>"
    assert msg["To"] == "a@x.io, b@x.io"
    kwargs = ctor.call_args.kwargs
    assert kwargs["hostname"] == "smtp.x.io"
    assert kwargs["port"] == 587
    assert kwargs["start_tls"] is True
    assert kwargs["use_tls"] is False
    client.login.assert_awaited_once_with("u", "p")
    parts = [p.get_filename() for p in msg.iter_attachments()]
    assert parts == ["report.pdf"]


async def test_implicit_tls_mode_sets_use_tls():
    captured: dict = {}
    client = _mock_client(captured)
    with patch("app.services.email.smtp.aiosmtplib.SMTP", return_value=client) as ctor:
        await send_report_email(
            _cfg(security="tls", port=465),
            subject="s", recipients=["a@x.io"], body_text="b",
            attachment=("r.pdf", b"%PDF-", "application/pdf"),
        )
    kwargs = ctor.call_args.kwargs
    assert kwargs["use_tls"] is True
    assert kwargs["start_tls"] is False


async def test_no_username_skips_auth():
    captured: dict = {}
    client = _mock_client(captured)
    with patch("app.services.email.smtp.aiosmtplib.SMTP", return_value=client):
        await send_report_email(
            _cfg(username=None, password=None),
            subject="s", recipients=["a@x.io"], body_text="b",
            attachment=("r.pdf", b"%PDF-", "application/pdf"),
        )
    client.login.assert_not_awaited()
    client.auth_xoauth2.assert_not_awaited()


async def test_transport_failure_wrapped():
    client = _mock_client({})
    client.__aenter__ = AsyncMock(side_effect=aiosmtplib.SMTPException("connection refused"))
    with patch("app.services.email.smtp.aiosmtplib.SMTP", return_value=client):
        with pytest.raises(EmailSendError):
            await send_report_email(
                _cfg(), subject="s", recipients=["a@x.io"], body_text="b",
                attachment=("r.pdf", b"%PDF-", "application/pdf"),
            )


async def test_header_injection_stripped():
    captured: dict = {}
    client = _mock_client(captured)
    with patch("app.services.email.smtp.aiosmtplib.SMTP", return_value=client):
        await send_report_email(
            _cfg(from_name="Evil\r\nBcc: victim@x.io"),
            subject="s\r\nX-Injected: 1", recipients=["a@x.io"], body_text="b",
            attachment=("r.pdf", b"%PDF-", "application/pdf"),
        )
    msg = captured["message"]
    assert "\n" not in msg["Subject"] and "\r" not in msg["Subject"]
    assert "Bcc" not in msg
