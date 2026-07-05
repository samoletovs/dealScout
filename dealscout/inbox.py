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
import re
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


def _fetch(criteria: str, *, mark_seen: bool, limit: int, mailbox: str = "INBOX") -> list[tuple[str, str, str]]:
    """Fetch messages matching an IMAP search in `mailbox`. Optionally mark \\Seen."""
    creds = _credentials()
    if creds is None:
        return []
    user, password, host = creds
    out: list[tuple[str, str, str]] = []
    try:
        with imaplib.IMAP4_SSL(host) as imap:
            imap.login(user, password)
            typ, _ = imap.select(f'"{mailbox}"', readonly=not mark_seen)
            if typ != "OK":
                logger.info("mailbox %s unavailable", mailbox)
                return []
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
    """Fetch ALL newsletters from the last `days` days across All Mail and Spam
    (read, unread, or archived) for the subscription-health summary. Scanning
    All Mail catches archived mail; Spam catches brands Gmail quarantined. The
    account's own sent digests are filtered out.
    """
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    criteria = f"(SINCE {since})"
    own = (os.getenv("DEALSCOUT_IMAP_USER") or "").lower()
    combined: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for mailbox in ("[Gmail]/All Mail", "[Gmail]/Spam"):
        for parts in _fetch(criteria, mark_seen=False, limit=limit, mailbox=mailbox):
            if own and own in parts[0].lower():
                continue  # skip our own sent digests
            key = (parts[0], parts[1])
            if key not in seen:
                seen.add(key)
                combined.append(parts)
    logger.info("scanned %d newsletter(s) in the last %d days (all mail + spam)", len(combined), days)
    return combined


def mailbox_counts() -> dict[str, int]:
    """Log total message counts per mailbox — a deliverability diagnostic that
    distinguishes 'nothing arrived' from 'mail is elsewhere'.
    """
    creds = _credentials()
    if creds is None:
        return {}
    user, password, host = creds
    counts: dict[str, int] = {}
    try:
        with imaplib.IMAP4_SSL(host) as imap:
            imap.login(user, password)
            for mailbox in ("INBOX", "[Gmail]/Spam", "[Gmail]/All Mail"):
                typ, data = imap.status(f'"{mailbox}"', "(MESSAGES)")
                if typ == "OK" and data and data[0]:
                    match = re.search(rb"MESSAGES\s+(\d+)", data[0])
                    counts[mailbox] = int(match.group(1)) if match else 0
    except (imaplib.IMAP4.error, OSError) as exc:
        logger.warning("mailbox status failed (%s)", exc)
        return {}
    logger.info("mailbox totals: %s", counts)
    return counts


def health() -> str:
    """Return mailbox login health: 'ok', 'auth_failed', or 'not_configured'.

    A monitor must never silently pass while unable to connect, so this cleanly
    separates a broken login from a genuinely empty inbox.
    """
    creds = _credentials()
    if creds is None:
        return "not_configured"
    user, password, host = creds
    try:
        with imaplib.IMAP4_SSL(host) as imap:
            imap.login(user, password)
        return "ok"
    except (imaplib.IMAP4.error, OSError) as exc:
        logger.error("IMAP login failed — update the Gmail app password secret (%s)", exc)
        return "auth_failed"
