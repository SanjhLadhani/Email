"""The agent's tools: look-ups it can choose to run *before* it decides.

This is what makes the triage step an agent rather than a fixed prompt. Given an
email, the model decides for itself whether it needs more context, calls one or
more tools, reads what they return, and only then produces its verdict. Pydantic AI
runs that loop (model -> tool call -> result -> model ...) until the model answers.

Every tool is **read-only**: none can send, delete, move or change anything. Their
results are shown to the model as data, never as instructions. A run is also capped
(see ``LIMITS``) so a confused model cannot loop forever.

Tools:

* ``sender_history``: earlier emails from this sender, and how they were rated.
* ``search_my_emails``: earlier emails mentioning a word (a project, a topic).
* ``did_i_already_reply``: whether a reply to a subject is already in Gmail Sent Mail.

``describe_steps`` turns a finished run into the short "what the agent checked"
list that the dashboard shows.
"""

import json
from dataclasses import dataclass

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ToolCallPart, ToolReturnPart
from pydantic_ai.usage import UsageLimits

from automation import step2_gmail_reader as gmail
from automation import step3_storage as storage
from agent.schemas import Email

# Hard ceilings for one email: at most 4 tool calls and 8 model requests.
LIMITS = UsageLimits(request_limit=8, tool_calls_limit=4)

_OFF = "Look-ups are switched off for this run; decide from the email alone."


@dataclass
class TriageContext:
    """What the tools may know about the email being analyzed."""

    email: Email
    allow_lookups: bool = True  # False for evals, so scores depend on the email alone


def _line(row) -> str:
    """One earlier email as a short line: date | urgency | status | subject."""
    when = (row["received_at"] or row["processed_at"] or "")[:10]
    return f"- {when} | {row['urgency']} | {row['status']} | {row['subject'][:70]}"


def sender_history(ctx: RunContext[TriageContext]) -> str:
    """List earlier emails from this same sender (or their domain) that the recipient has already seen.

    Shows each one's date, the urgency it was given, and whether the recipient
    dismissed it or drafted a reply. Use it when the sender is unfamiliar or when
    this might be part of a recurring pattern (a newsletter, a follow-up).
    """
    if not ctx.deps.allow_lookups:
        return _OFF
    rows = storage.sender_history(ctx.deps.email.sender, ctx.deps.email.message_id, limit=5)
    if not rows:
        return "No earlier emails from this sender."
    return f"{len(rows)} earlier email(s) from this sender or domain:\n" + "\n".join(_line(r) for r in rows)


def search_my_emails(ctx: RunContext[TriageContext], query: str) -> str:
    """Search emails the recipient has already received for a word in the subject, sender or summary.

    Use it to see whether a project, order number, person or topic mentioned in this
    email has come up before. Pass one short keyword, not a sentence.
    """
    if not ctx.deps.allow_lookups:
        return _OFF
    query = query.strip()[:60]
    if not query:
        return "Give a keyword to search for."
    rows = storage.search_emails(query, ctx.deps.email.message_id, limit=5)
    if not rows:
        return f"No earlier emails mention {query!r}."
    return f"{len(rows)} earlier email(s) mention {query!r}:\n" + "\n".join(_line(r) for r in rows)


def did_i_already_reply(ctx: RunContext[TriageContext], subject: str) -> str:
    """Check the recipient's Gmail Sent folder for a reply to this subject in the last 30 days.

    Use it before saying the recipient must respond to a reminder, a follow-up or
    a question: if they already replied, nothing is needed.
    """
    if not ctx.deps.allow_lookups:
        return _OFF
    try:
        when = gmail.find_sent_reply(subject)
    except Exception as exc:  # a failed look-up must not break the analysis
        return f"Could not check Sent mail: {exc}"
    if when is None:
        return "No reply to this subject in Sent mail in the last 30 days."
    return f"Yes: a message with this subject was sent on {when[:10]}."


TOOLS = [sender_history, search_my_emails, did_i_already_reply]
_LOOKUP_NAMES = {tool.__name__ for tool in TOOLS}


def _label(name: str, args: dict) -> str:
    """The plain-English name of a tool call, for the dashboard."""
    if name == "sender_history":
        return "Looked at earlier emails from this sender"
    if name == "search_my_emails":
        return f"Searched my emails for “{args.get('query', '')}”"
    if name == "did_i_already_reply":
        return f"Checked Sent mail for a reply to “{str(args.get('subject', ''))[:60]}”"
    return name


def describe_steps(messages: list[ModelMessage]) -> list[dict]:
    """Turn a finished run into ``[{"label": ..., "result": ...}]``, one per tool call.

    The label says what the agent looked up, the result is the start of what it
    got back. An empty list means it decided from the email alone.
    """
    calls: dict[str, ToolCallPart] = {}
    steps: list[dict] = []
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                calls[part.tool_call_id] = part
            elif isinstance(part, ToolReturnPart) and part.tool_call_id in calls:
                call = calls[part.tool_call_id]
                if call.tool_name not in _LOOKUP_NAMES:
                    continue  # the model's final answer is also a tool call; it is not a look-up
                args = call.args_as_dict() if hasattr(call, "args_as_dict") else json.loads(call.args or "{}")
                content = str(part.content)
                if content == _OFF:
                    continue  # a switched-off look-up tells the user nothing
                steps.append({
                    "label": _label(call.tool_name, args),
                    "result": " ".join(line.lstrip("- ") for line in content.splitlines()[:3])[:220],
                })
    return steps
