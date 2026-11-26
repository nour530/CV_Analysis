"""Sending email (password reset, email verification) over SMTP.

Any SMTP provider works: Brevo, Mailjet, Gmail, or the host's own server.
Email is off until SMTP_HOST is set; the app then keeps working, but no
message leaves the server (the endpoints still answer the same way).

    SMTP_HOST=smtp-relay.brevo.com
    SMTP_PORT=587                  # 465 means implicit TLS
    SMTP_USERNAME=...
    SMTP_PASSWORD=...              # from the environment, never committed
    SMTP_FROM="JobMatch AI <no-reply@example.com>"
    SMTP_STARTTLS=true
    APP_PUBLIC_URL=https://jobmatch.example.com   # links in emails point here

Message bodies carry single-use links. They are never logged.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from functools import lru_cache
from typing import Protocol

logger = logging.getLogger("jobmatch.mail")

DEFAULT_SENDER = "JobMatch AI <no-reply@localhost>"
DEFAULT_PUBLIC_URL = "http://localhost:4200"


@dataclass(frozen=True, slots=True)
class MailSettings:
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    sender: str = DEFAULT_SENDER
    starttls: bool = True
    public_url: str = DEFAULT_PUBLIC_URL
    timeout_seconds: int = 20

    @property
    def enabled(self) -> bool:
        return bool(self.host)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> MailSettings:
        env = os.environ if env is None else env
        try:
            port = int(env.get("SMTP_PORT") or 587)
        except ValueError:
            port = 587
        return cls(
            host=(env.get("SMTP_HOST") or "").strip(),
            port=port,
            username=(env.get("SMTP_USERNAME") or "").strip(),
            password=env.get("SMTP_PASSWORD") or "",
            sender=(env.get("SMTP_FROM") or DEFAULT_SENDER).strip(),
            starttls=(env.get("SMTP_STARTTLS") or "true").strip().casefold() not in {"0", "false", "no", "off"},
            public_url=(env.get("APP_PUBLIC_URL") or DEFAULT_PUBLIC_URL).strip().rstrip("/"),
        )


class Mailer(Protocol):
    settings: MailSettings

    def send(self, *, to: str, subject: str, text: str) -> None: ...


class SmtpMailer:
    def __init__(self, settings: MailSettings) -> None:
        self.settings = settings

    def send(self, *, to: str, subject: str, text: str) -> None:
        if not self.settings.enabled:
            logger.info("mail_disabled subject=%r (set SMTP_HOST to send email)", subject)
            return
        message = EmailMessage()
        message["From"] = self.settings.sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        context = ssl.create_default_context()
        try:
            if self.settings.port == 465:
                with smtplib.SMTP_SSL(self.settings.host, self.settings.port, timeout=self.settings.timeout_seconds, context=context) as smtp:
                    self._deliver(smtp, message)
            else:
                with smtplib.SMTP(self.settings.host, self.settings.port, timeout=self.settings.timeout_seconds) as smtp:
                    if self.settings.starttls:
                        smtp.starttls(context=context)
                    self._deliver(smtp, message)
        except (OSError, smtplib.SMTPException) as exc:
            # The recipient and body stay out of the log: the body holds a link.
            logger.error("mail_send_failed subject=%r error=%s", subject, type(exc).__name__)
            return
        logger.info("mail_sent subject=%r", subject)

    def _deliver(self, smtp: smtplib.SMTP, message: EmailMessage) -> None:
        if self.settings.username:
            smtp.login(self.settings.username, self.settings.password)
        smtp.send_message(message)


@lru_cache(maxsize=1)
def _mailer() -> SmtpMailer:
    return SmtpMailer(MailSettings.from_env())


def get_mailer() -> Mailer:
    """FastAPI dependency; tests override it with a recording fake."""
    return _mailer()
