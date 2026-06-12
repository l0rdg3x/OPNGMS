import aiosmtplib
import pytest

from app.services.email.smtp import EmailSendError, SmtpSendConfig, send_report_email


def _cfg(**over):
    base = dict(host="smtp.x.io", port=587, security="starttls", username="u",
                password="p", from_email="noc@x.io", from_name="OPNGMS NOC")
    base.update(over)
    return SmtpSendConfig(**base)


async def test_send_builds_message_with_pdf_attachment(monkeypatch):
    captured = {}

    async def fake_send(message, **kwargs):
        captured["message"] = message
        captured["kwargs"] = kwargs
        return ({}, "ok")

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
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
    assert captured["kwargs"]["hostname"] == "smtp.x.io"
    assert captured["kwargs"]["port"] == 587
    assert captured["kwargs"]["start_tls"] is True
    assert captured["kwargs"].get("use_tls") in (False, None)
    assert captured["kwargs"]["username"] == "u"
    parts = [p.get_filename() for p in msg.iter_attachments()]
    assert parts == ["report.pdf"]


async def test_implicit_tls_mode_sets_use_tls(monkeypatch):
    captured = {}

    async def fake_send(message, **kwargs):
        captured["kwargs"] = kwargs
        return ({}, "ok")

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    await send_report_email(
        _cfg(security="tls", port=465),
        subject="s", recipients=["a@x.io"], body_text="b",
        attachment=("r.pdf", b"%PDF-", "application/pdf"),
    )
    assert captured["kwargs"]["use_tls"] is True
    assert captured["kwargs"].get("start_tls") in (False, None)


async def test_no_username_skips_auth(monkeypatch):
    captured = {}

    async def fake_send(message, **kwargs):
        captured["kwargs"] = kwargs
        return ({}, "ok")

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    await send_report_email(
        _cfg(username=None, password=None),
        subject="s", recipients=["a@x.io"], body_text="b",
        attachment=("r.pdf", b"%PDF-", "application/pdf"),
    )
    assert captured["kwargs"].get("username") is None


async def test_transport_failure_wrapped(monkeypatch):
    async def boom(message, **kwargs):
        raise aiosmtplib.SMTPException("connection refused")

    monkeypatch.setattr(aiosmtplib, "send", boom)
    with pytest.raises(EmailSendError):
        await send_report_email(
            _cfg(), subject="s", recipients=["a@x.io"], body_text="b",
            attachment=("r.pdf", b"%PDF-", "application/pdf"),
        )


async def test_header_injection_stripped(monkeypatch):
    captured = {}

    async def fake_send(message, **kwargs):
        captured["message"] = message
        return ({}, "ok")

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    await send_report_email(
        _cfg(from_name="Evil\r\nBcc: victim@x.io"),
        subject="s\r\nX-Injected: 1", recipients=["a@x.io"], body_text="b",
        attachment=("r.pdf", b"%PDF-", "application/pdf"),
    )
    msg = captured["message"]
    assert "\n" not in msg["Subject"] and "\r" not in msg["Subject"]
    assert "Bcc" not in msg
