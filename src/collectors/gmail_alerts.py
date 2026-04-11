"""
Gmail Alerts Collector — PRIMARY data source.

Fetches PropertyGuru and 99.co email alerts from Gmail using the
Gmail API (OAuth2, read-only scope).

SETUP REQUIRED (one-time, ~10 min):
  1. Go to https://console.cloud.google.com
  2. Create a project → Enable Gmail API
  3. OAuth 2.0 credentials → Desktop app → Download as config/credentials.json
  4. Run `python run.py --now` once to trigger the browser OAuth flow
  5. token.json will be saved to config/ for future runs
"""
from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseListing, BaseCollector
from ..processor.parser import parse_propertyguru_email, parse_99co_email

CREDENTIALS_PATH = Path(__file__).parent.parent.parent / "config" / "credentials.json"
TOKEN_PATH = Path(__file__).parent.parent.parent / "config" / "token.json"

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailAlertsCollector(BaseCollector):
    """
    Fetch rental alerts from Gmail labels.

    User pre-requisites:
      - Gmail filter: from:noreply@propertyguru.com.sg → label: Rentals/PropertyGuru
      - Gmail filter: from:@mail.99.co → label: Rentals/99co   (optional)
      - PropertyGuru saved search with daily email alerts enabled
    """

    source_name = "Gmail Alerts"

    PG_LABEL = "Rentals/PropertyGuru"
    NINETY_LABEL = "Rentals/99co"
    PG_SENDER = "noreply@propertyguru.com.sg"
    NINETY_SENDER = "mail.99.co"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.since_hours: int = self.config.get("since_hours", 72)
        self._service = None

    def authenticate(self):
        """Perform OAuth2 flow or load cached token. Returns Gmail service."""
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError:
            raise RuntimeError(
                "Google API client libraries not installed.\n"
                "Run: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
            )

        creds = None

        if TOKEN_PATH.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not CREDENTIALS_PATH.exists():
                    raise FileNotFoundError(
                        f"Gmail OAuth credentials not found at {CREDENTIALS_PATH}\n"
                        "Follow setup in README.md Step 3 to create credentials.json"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CREDENTIALS_PATH), SCOPES
                )
                creds = flow.run_local_server(port=0)

            TOKEN_PATH.write_text(creds.to_json())
            print(f"[Gmail] OAuth token saved to {TOKEN_PATH}")

        self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def collect(self) -> list[BaseListing]:
        """Fetch listings from both PropertyGuru and 99.co email alerts."""
        if self._service is None:
            self.authenticate()

        listings: list[BaseListing] = []

        # PropertyGuru alerts
        try:
            pg_listings = self._fetch_from_label(self.PG_LABEL, "propertyguru")
            listings.extend(pg_listings)
            print(f"[Gmail] PropertyGuru email alerts: {len(pg_listings)} listings")
        except Exception as e:
            print(f"[Gmail] PropertyGuru fetch failed: {e}")
            if "Label not found" in str(e):
                print(
                    "\n[Gmail] SETUP NEEDED: Create Gmail label 'Rentals/PropertyGuru'\n"
                    "  Gmail → Settings → Filters → Create filter:\n"
                    "  from: noreply@propertyguru.com.sg → Apply label: Rentals/PropertyGuru\n"
                )

        # 99.co alerts (optional)
        try:
            ninety_listings = self._fetch_from_label(self.NINETY_LABEL, "99co")
            listings.extend(ninety_listings)
            print(f"[Gmail] 99.co email alerts: {len(ninety_listings)} listings")
        except Exception as e:
            # 99.co label is optional — soft fail
            print(f"[Gmail] 99.co fetch skipped: {e}")

        return listings

    def fetch_recent_alerts(self, since_hours: Optional[int] = None) -> list[BaseListing]:
        """Public alias for collect() with optional time override."""
        if since_hours:
            self.since_hours = since_hours
        return self.collect()

    # ── Private helpers ───────────────────────────────────────────────────

    def _fetch_from_label(self, label_name: str, source_type: str) -> list[BaseListing]:
        """Fetch all emails from a label and parse listings."""
        label_id = self._get_label_id(label_name)
        if not label_id:
            raise ValueError(f"Label not found: '{label_name}'")

        since_date = datetime.now(timezone.utc) - timedelta(hours=self.since_hours)
        after_ts = int(since_date.timestamp())

        query = f"label:{label_id} after:{after_ts}"
        result = self._service.users().messages().list(
            userId="me", q=query, maxResults=100
        ).execute()

        messages = result.get("messages", [])
        listings: list[BaseListing] = []

        for msg_ref in messages:
            try:
                msg = self._service.users().messages().get(
                    userId="me", id=msg_ref["id"], format="full"
                ).execute()
                html_body = self._extract_html_body(msg)
                if not html_body:
                    continue

                if source_type == "propertyguru":
                    new_listings = parse_propertyguru_email(html_body)
                elif source_type == "99co":
                    new_listings = parse_99co_email(html_body)
                else:
                    new_listings = []

                listings.extend(new_listings)
            except Exception as e:
                print(f"[Gmail] Error parsing message {msg_ref['id']}: {e}")
                continue

        return listings

    def _get_label_id(self, label_name: str) -> Optional[str]:
        """Resolve label display name to Gmail label ID."""
        result = self._service.users().labels().list(userId="me").execute()
        for label in result.get("labels", []):
            if label["name"].lower() == label_name.lower():
                return label["id"]
        return None

    def _extract_html_body(self, message: dict) -> Optional[str]:
        """Extract HTML body from a Gmail message (handles multipart)."""
        payload = message.get("payload", {})
        return self._find_html_part(payload)

    def _find_html_part(self, part: dict) -> Optional[str]:
        """Recursively find the HTML part in a multipart message."""
        mime_type = part.get("mimeType", "")

        if mime_type == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

        for subpart in part.get("parts", []):
            result = self._find_html_part(subpart)
            if result:
                return result

        return None
