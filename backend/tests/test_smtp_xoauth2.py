from unittest.mock import AsyncMock, MagicMock, patch

from app.services.email.smtp import SmtpSendConfig, send_email


def _cfg(**kw):
    base = dict(host="smtp.gmail.com", port=587, security="starttls", username="me@x.com",
                password=None, from_email="me@x.com", from_name="Me")
    base.update(kw)
    return SmtpSendConfig(**base)


async def test_send_uses_xoauth2_when_access_token_set():
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.auth_xoauth2 = AsyncMock()
    client.login = AsyncMock()
    client.send_message = AsyncMock()
    with patch("app.services.email.smtp.aiosmtplib.SMTP", return_value=client):
        await send_email(_cfg(access_token="ya29.tok"), subject="s", recipients=["to@x.com"],
                         body_text="b")
    client.auth_xoauth2.assert_awaited_once_with("me@x.com", "ya29.tok")
    client.login.assert_not_awaited()
    client.send_message.assert_awaited_once()
