"""Step 2 - Read emails from Gmail over IMAP (read-only).

Uses a Gmail app password (see ``.env``). The mailbox is opened read-only and
messages are fetched with ``BODY.PEEK``, so nothing is marked as read, moved
or deleted.

Run directly to print your latest unread emails::

    python -m automation.step2_gmail_reader
"""

import email
import imaplib
import re
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime

import config
from agent.schemas import Email


def _decode(value: str | None) -> str:
    """Decode a possibly RFC 2047-encoded header into plain text."""
    return str(make_header(decode_header(value or "")))


def _received_at(msg: Message) -> str | None:
    """Return the message's ``Date`` header as an ISO-8601 UTC string, or None."""
    try:
        when = parsedate_to_datetime(msg["Date"])
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).isoformat()


def _extract_body(msg: Message) -> str:
    """Return the readable text of a message.

    Prefers the ``text/plain`` part; falls back to the ``text/html`` part with
    tags stripped.
    """
    plain, html = "", ""
    for part in msg.walk():
        if part.get_content_maintype() != "text" or part.get_filename():
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if part.get_content_subtype() == "plain" and not plain:
            plain = text
        elif part.get_content_subtype() == "html" and not html:
            html = text
    if plain:
        return plain.strip()
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _imap_date(day: date) -> str:
    """Format a date the way IMAP wants it ('27-Oct-2026'), whatever the locale."""
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def _numbers_between(imap: imaplib.IMAP4_SSL, criteria: list[str], since: date, until: date) -> list[bytes]:
    """Message numbers (oldest first) whose *local* date is between ``since`` and ``until``.

    The server only searches by whole days in its own time zone, so it is asked
    for one extra day on each side and the exact local date is checked from each
    message's ``Date`` header.
    """
    _, data = imap.search(
        None, *criteria, "SINCE", _imap_date(since - timedelta(days=1)),
        "BEFORE", _imap_date(until + timedelta(days=2)),
    )
    candidates = data[0].split()
    kept: list[bytes] = []
    for start in range(0, len(candidates), 200):
        chunk = candidates[start:start + 200]
        _, rows = imap.fetch(b",".join(chunk), "(BODY.PEEK[HEADER.FIELDS (DATE)])")
        for row in rows:
            if not isinstance(row, tuple):
                continue
            iso = _received_at(email.message_from_bytes(row[1]))
            if iso is None:
                continue
            local_day = datetime.fromisoformat(iso).astimezone().date()
            if since <= local_day <= until:
                kept.append(row[0].split()[0])
    return sorted(kept, key=int)


def fetch_emails(
    limit: int = 10,
    unread_only: bool = True,
    since: date | None = None,
    until: date | None = None,
) -> list[Email]:
    """Fetch the most recent emails from the Gmail inbox.

    Args:
        limit: Maximum number of emails to return (newest first).
        unread_only: If True, only return unread emails.
        since: First day to include (your local date). Leave both dates empty
            to take the newest emails regardless of day.
        until: Last day to include. Defaults to ``since``.

    Returns:
        A list of ``Email`` objects. ``message_id`` is the RFC 822
        Message-ID header, which stays stable across sessions.
    """
    address, password = config.require_email_credentials()
    emails: list[Email] = []
    criteria = ["UNSEEN"] if unread_only else ["ALL"]
    with imaplib.IMAP4_SSL(config.IMAP_HOST) as imap:
        imap.login(address, password)
        imap.select("INBOX", readonly=True)
        if since or until:
            since = since or until
            nums = _numbers_between(imap, criteria, since, until or since)
        else:
            _, data = imap.search(None, *criteria)
            nums = data[0].split()
        for num in reversed(nums[-limit:]):
            _, parts = imap.fetch(num, "(BODY.PEEK[])")
            msg = email.message_from_bytes(parts[0][1])
            emails.append(
                Email(
                    message_id=(msg["Message-ID"] or f"imap-{num.decode()}").strip(),
                    sender=_decode(msg["From"]),
                    subject=_decode(msg["Subject"]),
                    body=_extract_body(msg),
                    received_at=_received_at(msg),
                    to=_decode(msg["To"]) or None,
                    cc=_decode(msg["Cc"]) or None,
                )
            )
    return emails


def unread_count() -> int:
    """How many unread emails the Gmail inbox holds in total (read-only, one cheap IMAP STATUS call)."""
    address, password = config.require_email_credentials()
    with imaplib.IMAP4_SSL(config.IMAP_HOST) as imap:
        imap.login(address, password)
        _, data = imap.status("INBOX", "(UNSEEN)")
    match = re.search(rb"UNSEEN (\d+)", data[0])
    return int(match.group(1)) if match else 0


def fetch_body(message_id: str) -> str | None:
    """Read one email's text from Gmail by its Message-ID (bodies are not stored locally).

    Returns None if the email is no longer in the inbox.
    """
    address, password = config.require_email_credentials()
    with imaplib.IMAP4_SSL(config.IMAP_HOST) as imap:
        imap.login(address, password)
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, "HEADER", "Message-ID", f'"{message_id}"')
        nums = data[0].split()
        if not nums:
            return None
        _, parts = imap.fetch(nums[-1], "(BODY.PEEK[])")
        return _extract_body(email.message_from_bytes(parts[0][1]))


SENT_FOLDER = '"[Gmail]/Sent Mail"'


def find_sent_reply(subject: str, days: int = 30) -> str | None:
    """Return the date (ISO UTC) of the newest message in Sent Mail about ``subject``, or None.

    Read-only. ``Re:`` / ``Fwd:`` prefixes are ignored so a reply matches the original.
    (The folder name is for English-language Gmail.)

    Raises:
        ValueError: If the subject has characters the IMAP search cannot handle.
    """
    core = re.sub(r"^(\s*(re|fwd?|aw)\s*:\s*)+", "", subject, flags=re.IGNORECASE).strip()
    core = core.replace('"', "").replace("\\", "")[:80]
    if not core or not core.isascii():
        raise ValueError("this subject cannot be searched")
    address, password = config.require_email_credentials()
    with imaplib.IMAP4_SSL(config.IMAP_HOST) as imap:
        imap.login(address, password)
        imap.select(SENT_FOLDER, readonly=True)
        _, data = imap.search(None, "SINCE", _imap_date(date.today() - timedelta(days=days)), "SUBJECT", f'"{core}"')
        nums = data[0].split()
        if not nums:
            return None
        _, parts = imap.fetch(nums[-1], "(BODY.PEEK[HEADER.FIELDS (DATE)])")
        return _received_at(email.message_from_bytes(parts[0][1]))


def fetch_received_dates(message_ids: list[str]) -> dict[str, str]:
    """Look up the sent date of emails we already stored.

    Args:
        message_ids: RFC 822 Message-IDs to look up in the inbox.

    Returns:
        ``{message_id: iso_utc_date}`` for the ones still found in the inbox.
        Emails that were archived or deleted are simply missing from the result.
    """
    address, password = config.require_email_credentials()
    found: dict[str, str] = {}
    with imaplib.IMAP4_SSL(config.IMAP_HOST) as imap:
        imap.login(address, password)
        imap.select("INBOX", readonly=True)
        for message_id in message_ids:
            _, data = imap.search(None, "HEADER", "Message-ID", f'"{message_id}"')
            nums = data[0].split()
            if not nums:
                continue
            _, parts = imap.fetch(nums[-1], "(BODY.PEEK[HEADER.FIELDS (DATE)])")
            when = _received_at(email.message_from_bytes(parts[0][1]))
            if when:
                found[message_id] = when
    return found


if __name__ == "__main__":
    for item in fetch_emails(limit=5):
        print(f"{item.sender} | {item.subject}\n  {item.body[:100]!r}\n")
