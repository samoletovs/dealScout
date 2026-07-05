"""Read recent newsletters from the dedicated Gmail via IMAP.

Configure with env: DEALSCOUT_IMAP_HOST (default imap.gmail.com),
DEALSCOUT_IMAP_USER, DEALSCOUT_IMAP_PASS (a Gmail App Password — never commit).
Read-only peek at UNSEEN messages. Returns [] if not configured, so local/CI
runs never fail. The message parsing (`extract_parts`) is pure and testable.
"""

from __future__ import annotations

import email
import imaplib
import logging
import os
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.message import Message

logger = logging.getLogger(__name__)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, LookupError):
        return value


def _html_body(msg: Message) -> str:
    """Best-effort HTML (falling back to plain text) body of a message."""
    if not msg.is_multipart():
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        return ""

    html = ""
    text = ""
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/html":
            html = html or body
        elif part.get_content_type() == "text/plain":
            text = text or body
    return html or text


def extract_parts(raw_email: bytes) -> tuple[str, str, str]:
    """Parse a raw RFC822 email into (sender, subject, html_body). Pure/testable."""
    msg = email.message_from_bytes(raw_email)
    return _decode(msg.get("From")), _decode(msg.get("Subject")), _html_body(msg)


def _credentials() -> tuple[str, str, str] | None:
    user = os.getenv("DEALSCOUT_IMAP_USER")
    password = os.getenv("DEALSCOUT_IMAP_PASS")
    if not user or not password:
        logger.warning("IMAP not configured (DEALSCOUT_IMAP_USER/PASS) — inbox unavailable")
        return None
    return user, password, os.getenv("DEALSCOUT_IMAP_HOST") or "imap.gmail.com"


def _fetch(criteria: str, *, mark_seen: bool, limit: int) -> list[tuple[str, str, str]]:
    """Fetch messages matching an IMAP search. Optionally mark them \\Seen."""
    creds = _credentials()
    if creds is None:
        return []
    user, password, host = creds
    out: list[tuple[str, str, str]] = []
    try:
        with imaplib.IMAP4_SSL(host) as imap:
            imap.login(user, password)
            imap.select("INBOX", readonly=not mark_seen)
            typ, data = imap.search(None, criteria)
            if typ != "OK":
                return []
            ids = data[0].split()[-limit:]
            for msg_id in ids:
                typ, msg_data = imap.fetch(msg_id, "(BODY.PEEK[])")
                if typ == "OK" and msg_data and msg_data[0]:
                    out.append(extract_parts(msg_data[0][1]))
            if mark_seen and ids:
                imap.store(",".join(i.decode() for i in ids), "+FLAGS", "\\Seen")
    except (imaplib.IMAP4.error, OSError) as exc:
        logger.warning("inbox read failed (%s) — skipping", exc)
        return []
    return out


def fetch_recent(limit: int = 100) -> list[tuple[str, str, str]]:
    """Fetch UNSEEN newsletters for deal alerts, then mark them read so the same
    sale isn't re-alerted next run. Returns [] if IMAP is not configured.
    """
    out = _fetch("UNSEEN", mark_seen=True, limit=limit)
    logger.info("fetched %d new newsletter(s) for deals", len(out))
    return out


def fetch_since(days: int = 7, limit: int = 300) -> list[tuple[str, str, str]]:
    """Fetch ALL newsletters from the last `days` days (read or unread) for the
    subscription-health summary. Does not change flags.
    """
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    out = _fetch(f"(SINCE {since})", mark_seen=False, limit=limit)
    logger.info("scanned %d newsletter(s) in the last %d days for senders", len(out), days)
    return out
