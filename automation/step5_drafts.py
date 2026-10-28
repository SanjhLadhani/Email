"""Step 5 - Save an approved reply as a Gmail *draft*. Nothing is ever sent.

The agent only suggests replies. After you approve one in the dashboard, this
module places it in your Gmail Drafts folder so you can review and send it
yourself from Gmail. The draft is threaded under the original conversation.
"""

import imaplib
import re
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, parseaddr

import config
from agent import user_profile
from agent.schemas import is_noreply

DRAFTS_FOLDER = "[Gmail]/Drafts"
SENT_FOLDER = "[Gmail]/Sent Mail"


def build_draft(sender: str, subject: str, body: str, message_id: str | None = None) -> EmailMessage:
    """Build a reply message addressed to the original sender.

    Args:
        sender: The original email's ``From`` header.
        subject: The original subject; ``Re: `` is prepended if missing.
        body: The reply text (already reviewed/approved by the user).
        message_id: The original Message-ID, so Gmail files the draft in the
            same conversation.

    Raises:
        ValueError: If the sender is a no-reply address or has no address.
    """
    to_name, to_address = parseaddr(sender)
    if not to_address or is_noreply(sender):
        raise ValueError("This sender does not accept replies (no-reply address).")
    address, _ = config.require_email_credentials()

    msg = EmailMessage()
    msg["From"] = formataddr((user_profile.signature_name(), address))
    msg["To"] = formataddr((to_name, to_address))
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    msg["Date"] = formatdate(localtime=True)
    if message_id:
        msg["In-Reply-To"] = message_id
        msg["References"] = message_id
    msg.set_content(body.strip() + "\n")
    return msg


def draft_states(drafted: list[tuple[str, str]]) -> dict[str, str]:
    """Where each reply you drafted here ended up, by looking in Gmail (read-only).

    Args:
        drafted: ``(original Message-ID, original subject)`` for every email in the *drafted* state.

    Returns:
        ``{message_id: state}`` where state is ``"draft"`` (still in Gmail Drafts), ``"sent"`` (a reply
        is in Sent Mail) or ``"gone"`` (neither: you deleted the draft). Drafts are matched through the
        ``In-Reply-To`` header MailMind put on them; a sent reply is also recognised by its subject.
    """
    from automation import step2_gmail_reader as gmail  # local import: step2 does not depend on this module

    usable = [(i, s) for i, s in drafted if i.isascii() and '"' not in i and "\\" not in i]
    address, password = config.require_email_credentials()
    states: dict[str, str] = {}
    with imaplib.IMAP4_SSL(config.IMAP_HOST) as imap:
        imap.login(address, password)

        def replies_to(folder: str, ids: list[str]) -> set[str]:
            imap.select(f'"{folder}"', readonly=True)
            return {i for i in ids if imap.search(None, "HEADER", "In-Reply-To", f'"{i}"')[1][0].split()}

        ids = [i for i, _ in usable]
        for found in replies_to(DRAFTS_FOLDER, ids):
            states[found] = "draft"
        rest = [i for i in ids if i not in states]
        for found in replies_to(SENT_FOLDER, rest):
            states[found] = "sent"
    for message_id, subject in usable:
        if message_id in states:
            continue
        try:  # a reply sent from Gmail normally keeps In-Reply-To; fall back to its subject
            states[message_id] = "sent" if gmail.find_sent_reply(subject) else "gone"
        except Exception:
            states[message_id] = "draft"  # cannot tell: leave it alone
    return states


def save_draft(sender: str, subject: str, body: str, message_id: str | None = None) -> None:
    """Append a reply to the Gmail Drafts folder.

    Raises:
        ValueError: If the sender cannot be replied to.
        RuntimeError: If credentials are missing or Gmail rejects the draft.
    """
    msg = build_draft(sender, subject, body, message_id)
    address, password = config.require_email_credentials()
    with imaplib.IMAP4_SSL(config.IMAP_HOST) as imap:
        imap.login(address, password)
        status, _ = imap.append(
            DRAFTS_FOLDER, "\\Draft", imaplib.Time2Internaldate(time.time()),
            msg.as_bytes(),
        )
    if status != "OK":
        raise RuntimeError(f"Gmail refused the draft: {status}")
