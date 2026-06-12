"""SMTP transport for report delivery. Pure transport: build a MIME message and send it.

Knows nothing about reports/tenants — callers pass a resolved config + message parts.
"""
from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage

import aiosmtplib


class EmailSendError(Exception):
    """Wraps any SMTP transport/auth failure so callers catch one type."""


@dataclass
class SmtpSendConfig:
    host: str
    port: int
    security: str  # "starttls" | "tls" | "none"
    username: str | None
    password: str | None
    from_email: str
    from_name: str


def _strip(value: str) -> str:
    # Defuse header injection: no CR/LF in any header-derived value.
    return value.replace("\r", " ").replace("\n", " ").strip()


def _safe_smtp_error(exc: Exception) -> str:
    msg = str(exc)
    idx = msg.upper().find("AUTH")
    if idx != -1:
        msg = msg[:idx] + "AUTH <redacted>"
    return msg[:200]


def _build_message(
    cfg: SmtpSendConfig,
    *,
    subject: str,
    recipients: list[str],
    body_text: str,
    attachment: tuple[str, bytes, str],
) -> EmailMessage:
    msg = EmailMessage()
    from_name = _strip(cfg.from_name)
    msg["From"] = f"{from_name} <{cfg.from_email}>" if from_name else cfg.from_email
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = _strip(subject)
    msg.set_content(body_text)
    filename, data, mime = attachment
    maintype, _, subtype = mime.partition("/")
    msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=filename)
    return msg


async def send_report_email(
    cfg: SmtpSendConfig,
    *,
    subject: str,
    recipients: list[str],
    body_text: str,
    attachment: tuple[str, bytes, str],
) -> None:
    """Send one email with a single attachment. Raises EmailSendError on any failure."""
    message = _build_message(
        cfg, subject=subject, recipients=recipients, body_text=body_text, attachment=attachment
    )
    kwargs: dict = {
        "hostname": cfg.host,
        "port": cfg.port,
        "start_tls": cfg.security == "starttls",
        "use_tls": cfg.security == "tls",
    }
    if cfg.username:
        kwargs["username"] = cfg.username
        kwargs["password"] = cfg.password or ""
    try:
        await aiosmtplib.send(message, **kwargs)
    except (aiosmtplib.SMTPException, OSError) as exc:
        raise EmailSendError(_safe_smtp_error(exc)) from exc
