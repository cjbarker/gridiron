"""Transactional email delivery.

Two transports: real SMTP (used when ``SMTP_HOST`` is configured) and a console
transport that logs the message to stdout (the zero-setup dev default). Callers
use the module-level :func:`send_email`; tests monkeypatch :func:`get_email_sender`.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from gridiron.config import get_settings

logger = logging.getLogger(__name__)


class EmailSender:
    def send(self, to: str, subject: str, body: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleEmailSender(EmailSender):
    """Logs the email instead of sending it — used when SMTP is unconfigured."""

    def send(self, to: str, subject: str, body: str) -> None:
        logger.warning("[email:console] to=%s subject=%r\n%s", to, subject, body)


class SmtpEmailSender(EmailSender):
    def __init__(self, host, port, user, password, from_addr, starttls):
        self.host, self.port = host, port
        self.user, self.password = user, password
        self.from_addr, self.starttls = from_addr, starttls

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port) as smtp:
            if self.starttls:
                smtp.starttls()
            if self.user:
                smtp.login(self.user, self.password or "")
            smtp.send_message(msg)


def get_email_sender() -> EmailSender:
    s = get_settings()
    if s.smtp_host:
        return SmtpEmailSender(
            s.smtp_host, s.smtp_port, s.smtp_user, s.smtp_password, s.smtp_from, s.smtp_starttls
        )
    return ConsoleEmailSender()


def send_email(to: str, subject: str, body: str) -> None:
    """Send via the configured transport; failures are logged, not raised."""
    try:
        get_email_sender().send(to, subject, body)
    except Exception:  # never let email failure break the request flow
        logger.exception("failed to send email to %s", to)
