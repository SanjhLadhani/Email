"""Step 3 - SQLite memory: remember processed emails and their analyses.

Stores every analysis keyed by the email's Message-ID so the agent never
analyzes the same email twice, and tracks a review ``status`` for the
dashboard (``new``, ``dismissed``, ``drafted`` while a reply sits in Gmail Drafts, and
``replied`` once it has been sent).
"""

import json
import sqlite3
from contextlib import contextmanager
from email.utils import parseaddr
from typing import Iterator

import config
from agent.schemas import Email, EmailAnalysis

URGENCY_ORDER = "CASE urgency WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    message_id       TEXT PRIMARY KEY,
    sender           TEXT NOT NULL,
    subject          TEXT NOT NULL,
    category         TEXT NOT NULL,
    urgency          TEXT NOT NULL,
    summary          TEXT NOT NULL,
    reason           TEXT NOT NULL,
    requires_action  INTEGER NOT NULL,
    suggested_reply  TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'new',
    processed_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,          -- 'urgency' or 'reply'
    sender      TEXT NOT NULL,
    subject     TEXT NOT NULL,
    urgency     TEXT,
    category    TEXT,
    reply       TEXT,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Open the database (creating the table if needed) and commit on exit."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(emails)")}
        # Databases created before these columns existed are upgraded in place.
        for column, definition in (
            ("received_at", "TEXT"),
            ("sender_is_person", "INTEGER NOT NULL DEFAULT 1"),
            ("reply_name", "TEXT NOT NULL DEFAULT ''"),
            ("agent_steps", "TEXT NOT NULL DEFAULT '[]'"),
        ):
            if column not in columns:
                conn.execute(f"ALTER TABLE emails ADD COLUMN {column} {definition}")
        yield conn
        conn.commit()
    finally:
        conn.close()


def is_processed(message_id: str) -> bool:
    """Return True if an email with this Message-ID was already analyzed."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM emails WHERE message_id = ?", (message_id,)
        ).fetchone()
    return row is not None


def save_result(email: Email, analysis: EmailAnalysis) -> None:
    """Store (or replace) the analysis for an email.

    Args:
        email: The source email; its ``message_id`` must be set.
        analysis: The agent's verdict for that email.

    Raises:
        ValueError: If the email has no ``message_id``.
    """
    if not email.message_id:
        raise ValueError("Email needs a message_id to be stored")
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO emails
               (message_id, sender, subject, category, urgency, summary,
                reason, requires_action, suggested_reply, received_at,
                sender_is_person, reply_name, agent_steps)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                email.message_id,
                email.sender,
                email.subject,
                analysis.category.value,
                analysis.urgency.value,
                analysis.summary,
                analysis.reason,
                int(analysis.requires_action),
                analysis.suggested_reply,
                email.received_at,
                int(analysis.sender_is_person),
                analysis.reply_name,
                json.dumps(analysis.steps),
            ),
        )


def list_results(
    urgency: str | None = None, status: str | None = None
) -> list[sqlite3.Row]:
    """Return stored analyses, most urgent first and newest first within that.

    Args:
        urgency: Only rows with this urgency (e.g. ``"critical"``), or None.
        status: Only rows with this review status, or None.
    """
    clauses, params = [], []
    if urgency:
        clauses.append("urgency = ?")
        params.append(urgency)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        return conn.execute(
            f"SELECT * FROM emails {where} "
            f"ORDER BY {URGENCY_ORDER}, COALESCE(received_at, processed_at) DESC",
            params,
        ).fetchall()


def set_status(message_id: str, status: str) -> None:
    """Set the review status of a stored email (e.g. ``approved``)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE emails SET status = ? WHERE message_id = ?",
            (status, message_id),
        )


def update_reply(message_id: str, reply: str) -> None:
    """Save a user-edited reply draft for a stored email."""
    with _connect() as conn:
        conn.execute(
            "UPDATE emails SET suggested_reply = ? WHERE message_id = ?",
            (reply, message_id),
        )


def get_email(message_id: str) -> sqlite3.Row | None:
    """Return one stored email by Message-ID, or None."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM emails WHERE message_id = ?", (message_id,)
        ).fetchone()


def record_urgency_feedback(message_id: str, urgency: str) -> None:
    """Apply the user's urgency correction and remember it as a lesson.

    Does nothing if the urgency is unchanged. The correction updates the
    stored email and is saved in the ``feedback`` table so the agent can see
    it as an example the next time a similar email arrives.
    """
    row = get_email(message_id)
    if row is None or row["urgency"] == urgency:
        return
    with _connect() as conn:
        conn.execute("UPDATE emails SET urgency = ? WHERE message_id = ?", (urgency, message_id))
        conn.execute(
            "INSERT INTO feedback (kind, sender, subject, urgency, category) VALUES ('urgency', ?, ?, ?, ?)",
            (row["sender"], row["subject"], urgency, row["category"]),
        )


def record_reply_feedback(message_id: str, reply: str) -> None:
    """Remember a reply the user edited, as an example of their own style."""
    row = get_email(message_id)
    if row is None or not reply.strip() or reply.strip() == row["suggested_reply"].strip():
        return
    with _connect() as conn:
        conn.execute(
            "INSERT INTO feedback (kind, sender, subject, reply) VALUES ('reply', ?, ?, ?)",
            (row["sender"], row["subject"], reply.strip()),
        )


def feedback_examples(sender: str, kind: str, limit: int) -> list[sqlite3.Row]:
    """Return past corrections of one kind, same-sender-domain first, then newest.

    Args:
        sender: The incoming email's ``From`` header.
        kind: ``"urgency"`` or ``"reply"``.
        limit: Maximum number of examples.
    """
    domain = parseaddr(sender)[1].rsplit("@", 1)[-1].lower()
    pattern = f"%@{domain}%" if domain else "%"
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM feedback WHERE kind = ? "
            "ORDER BY (lower(sender) LIKE ?) DESC, id DESC LIMIT ?",
            (kind, pattern, limit),
        ).fetchall()


def sender_history(sender: str, exclude_id: str | None, limit: int = 5) -> list[sqlite3.Row]:
    """Earlier stored emails from the same address, then the same domain, newest first.

    Args:
        sender: The ``From`` header of the email being analyzed.
        exclude_id: Its Message-ID, so it is not listed as its own history.
        limit: Maximum number of rows.
    """
    address = parseaddr(sender)[1].lower()
    domain = address.rsplit("@", 1)[-1]
    if not address:
        return []
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM emails WHERE lower(sender) LIKE ? AND message_id != ? "
            "ORDER BY (lower(sender) LIKE ?) DESC, COALESCE(received_at, processed_at) DESC LIMIT ?",
            (f"%@{domain}%", exclude_id or "", f"%{address}%", limit),
        ).fetchall()


def search_emails(query: str, exclude_id: str | None, limit: int = 5) -> list[sqlite3.Row]:
    """Stored emails whose subject, sender or summary contains ``query``, newest first."""
    like = f"%{query.lower()}%"
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM emails WHERE message_id != ? AND "
            "(lower(subject) LIKE ? OR lower(sender) LIKE ? OR lower(summary) LIKE ?) "
            "ORDER BY COALESCE(received_at, processed_at) DESC LIMIT ?",
            (exclude_id or "", like, like, like, limit),
        ).fetchall()


def handled_ids() -> list[str]:
    """Message-IDs of emails you dismissed, drafted a reply for, or replied to."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT message_id FROM emails WHERE status IN ('dismissed', 'drafted', 'replied') "
            "AND message_id NOT LIKE 'imap-%'"
        ).fetchall()
    return [row["message_id"] for row in rows]


def drafted_rows() -> list[tuple[str, str]]:
    """``(Message-ID, subject)`` of every email whose reply sits in the *drafted* state."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT message_id, subject FROM emails WHERE status = 'drafted' AND message_id NOT LIKE 'imap-%'"
        ).fetchall()
    return [(row["message_id"], row["subject"]) for row in rows]


def missing_received_dates() -> list[str]:
    """Return Message-IDs of stored emails that have no sent date yet."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT message_id FROM emails "
            "WHERE received_at IS NULL AND message_id NOT LIKE 'imap-%'"
        ).fetchall()
    return [row["message_id"] for row in rows]


def set_received_at(message_id: str, received_at: str) -> None:
    """Store the sent date (ISO-8601 UTC) of an email."""
    with _connect() as conn:
        conn.execute(
            "UPDATE emails SET received_at = ? WHERE message_id = ?",
            (received_at, message_id),
        )
