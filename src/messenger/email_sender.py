"""
Gmail SMTP Email Sender — sends the digest HTML email.

Uses Gmail App Password (not OAuth) for sending.
The same Gmail account is used for both reading alerts (OAuth) and
sending the digest (App Password SMTP).

Prerequisites:
  - Gmail 2FA must be enabled
  - Create an App Password at https://myaccount.google.com/apppasswords
  - Set GMAIL_USER and GMAIL_APP_PASSWORD in .env
"""
from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional


class EmailSender:
    """Send HTML digest emails via Gmail SMTP."""

    SMTP_HOST = "smtp.gmail.com"
    SMTP_PORT = 587

    def __init__(
        self,
        sender_email: Optional[str] = None,
        app_password: Optional[str] = None,
        recipient_email: Optional[str] = None,
    ):
        self.sender_email = sender_email or os.environ.get("GMAIL_USER", "")
        self.app_password = app_password or os.environ.get("GMAIL_APP_PASSWORD", "")
        self.recipient_email = recipient_email or os.environ.get("EMAIL_RECIPIENT", self.sender_email)

        if not self.sender_email:
            raise ValueError("GMAIL_USER not set in environment")
        if not self.app_password:
            raise ValueError(
                "GMAIL_APP_PASSWORD not set in environment.\n"
                "Create one at: https://myaccount.google.com/apppasswords\n"
                "(Requires Gmail 2FA to be enabled)"
            )

    def send(
        self,
        subject: str,
        html_body: str,
        recipient: Optional[str] = None,
        plain_text: Optional[str] = None,
    ) -> bool:
        """
        Send an HTML email via Gmail SMTP.

        Args:
            subject: Email subject line
            html_body: Full HTML content
            recipient: Override recipient (default: EMAIL_RECIPIENT env var)
            plain_text: Optional plain text fallback

        Returns:
            True on success, False on failure
        """
        to_addr = recipient or self.recipient_email
        if not to_addr:
            print("[EmailSender] No recipient address configured")
            return False

        msg = MIMEMultipart("alternative")
        msg["From"] = self.sender_email
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg["X-Mailer"] = "SG Rental Finder"

        # Attach plain text fallback
        if plain_text:
            msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        else:
            # Generate minimal plain text from HTML
            msg.attach(MIMEText(_html_to_plain(html_body), "plain", "utf-8"))

        # Attach HTML
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP(self.SMTP_HOST, self.SMTP_PORT) as server:
                server.ehlo()
                server.starttls()
                server.login(self.sender_email, self.app_password)
                server.sendmail(self.sender_email, to_addr, msg.as_string())
            print(f"[EmailSender] Digest sent to {to_addr}")
            return True
        except smtplib.SMTPAuthenticationError:
            print(
                "[EmailSender] Authentication failed.\n"
                "  - Check GMAIL_USER and GMAIL_APP_PASSWORD in .env\n"
                "  - App Password requires Gmail 2FA: https://myaccount.google.com/apppasswords"
            )
            return False
        except Exception as e:
            print(f"[EmailSender] Send failed: {e}")
            return False

    @staticmethod
    def preview(subject: str, html_body: str) -> None:
        """Print a plain text preview of the email (no send)."""
        print("=" * 70)
        print(f"SUBJECT: {subject}")
        print("=" * 70)
        print(_html_to_plain(html_body))
        print("=" * 70)


def _html_to_plain(html: str) -> str:
    """Convert HTML to plain text by stripping tags."""
    import re
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|div|tr|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
