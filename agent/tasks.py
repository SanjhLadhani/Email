"""Loads ``tasks.yaml``, where every prompt the agent uses lives.

The file is re-read whenever it changes on disk, so you can edit prompts while
autopilot is running. If the file is broken, a clear error names it instead of
the agent silently running with no instructions.
"""

from datetime import datetime
from pathlib import Path

import yaml

TASKS_PATH = Path(__file__).parent / "tasks.yaml"

_cache: dict = {"mtime": None, "data": None}


def load() -> dict:
    """Return the parsed ``tasks.yaml`` (cached until the file changes).

    Raises:
        RuntimeError: If the file is missing or is not valid YAML.
    """
    try:
        mtime = TASKS_PATH.stat().st_mtime
    except OSError as exc:
        raise RuntimeError(f"Cannot read {TASKS_PATH.name}: {exc}") from exc
    if _cache["mtime"] != mtime:
        try:
            _cache.update(mtime=mtime, data=yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8")))
        except yaml.YAMLError as exc:
            raise RuntimeError(f"{TASKS_PATH.name} is not valid YAML: {exc}") from exc
    return _cache["data"]


def agent_instructions() -> str:
    """Build the system prompt from the sections listed in ``order``."""
    section = load()["analyze_email"]["agent_instructions"]
    return "\n\n".join(section[name].strip() for name in section["order"])


def _received_text(received_at: str | None) -> str:
    """Format an ISO-8601 UTC time as local 'Mon 27 Oct 2026 14:10', or 'unknown'."""
    try:
        return datetime.fromisoformat(received_at).astimezone().strftime("%a %d %b %Y %H:%M")
    except (TypeError, ValueError):
        return "unknown"


def email_prompt(
    sender: str,
    subject: str,
    body: str,
    received_at: str | None = None,
    to: str | None = None,
    cc: str | None = None,
) -> str:
    """Render one email as the user message.

    Adds today's date (so "by Friday" can be judged) and, when known, when the
    email was received and who it was addressed to (To / Cc).
    """
    recipients = ""
    if to:
        recipients += f"To: {to}\n"
    if cc:
        recipients += f"Cc: {cc}\n"
    template = load()["analyze_email"]["email_prompt"]
    return template.format(
        today=datetime.now().astimezone().strftime("%A %d %B %Y"),
        received=_received_text(received_at),
        sender=sender,
        recipients=recipients,
        subject=subject,
        body=body,
    )


def rewrite_instructions() -> str:
    """The system prompt for the "rewrite this draft" button."""
    return load()["rewrite_reply"]["instructions"].strip()


def rewrite_prompt(sender: str, subject: str, body: str, draft: str, instruction: str) -> str:
    """Render the email, the current draft and the user's instruction as the user message."""
    t = load()["rewrite_reply"]
    return t["prompt"].format(
        sender=sender,
        subject=subject,
        body=body,
        draft=draft.strip() or t["no_draft"],
        instruction=instruction.strip() or t["write_new"],
    )


def past_decisions() -> dict:
    """The templates for showing the user's past corrections."""
    return load()["past_decisions"]


def profile_templates() -> dict:
    """The templates for the lines built from ``profile.json``."""
    return load()["profile"]
