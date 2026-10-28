"""Pydantic schemas shared by every step: the email going in, the verdict coming out.

Kept separate from the agent so storage, the API and the evals can import
them without pulling in the model.
"""

import re
from enum import Enum

from pydantic import AliasChoices, BaseModel, Field, PrivateAttr

from agent import tasks

MAX_BODY_CHARS = 4000

# Addresses that cannot receive a reply (donotreply@..., no-reply@..., etc.).
NOREPLY_SENDER = re.compile(
    r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer-daemon|postmaster|notifications?@|alerts?@|bounce)",
    re.IGNORECASE,
)


def is_noreply(sender: str) -> bool:
    """True if the ``From`` header is an address nobody reads replies at."""
    return bool(NOREPLY_SENDER.search(sender))


class Urgency(str, Enum):
    """How quickly an email needs attention."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EmailCategory(str, Enum):
    """What kind of email this is."""

    WORK = "work"
    PERSONAL = "personal"
    BILLING = "billing"
    PROMOTION = "promotion"
    SUPPORT = "support"
    OTHER = "other"


class Email(BaseModel):
    """An incoming email, as passed to the agent (input schema)."""

    message_id: str | None = Field(
        default=None, description="Unique id used to avoid re-processing"
    )
    sender: str
    subject: str
    body: str
    received_at: str | None = Field(
        default=None, description="ISO-8601 UTC time the email was sent"
    )
    to: str | None = Field(default=None, description="The To header (who it was addressed to)")
    cc: str | None = Field(default=None, description="The Cc header")

    def to_prompt(self) -> str:
        """Render the email as prompt text (template in tasks.yaml), truncating long bodies."""
        body = self.body[:MAX_BODY_CHARS]
        return tasks.email_prompt(self.sender, self.subject, body, self.received_at, self.to, self.cc)


class RewrittenReply(BaseModel):
    """The agent's output when it rewrites a draft (output schema)."""

    # Weaker models sometimes name the field after an email part instead of "reply".
    reply: str = Field(
        validation_alias=AliasChoices("reply", "body", "text", "message", "content"),
        description="The new reply body, without greeting or sign-off",
    )
    reply_name: str = Field(
        default="",
        description="First name of the person to greet; empty if the sender is a company "
        "or automated system, or no first name is known.",
    )
    sender_is_person: bool = Field(
        default=True,
        description="True only if an individual person wrote the email; False for "
        "companies, institutions and automated systems.",
    )


class EmailAnalysis(BaseModel):
    """The agent's structured verdict on one email (output schema)."""

    category: EmailCategory
    urgency: Urgency
    summary: str = Field(description="A concise summary of the email")
    reason: str = Field(description="Why this urgency was assigned")
    requires_action: bool = Field(
        description="True if the recipient has to do something"
    )
    sender_is_person: bool = Field(
        default=True,
        description="True only if an individual person wrote this to the recipient. "
        "False for companies, institutions, registrars, banks and automated systems.",
    )
    reply_name: str = Field(
        default="",
        description="First name of the person to greet in the reply; empty if the "
        "sender is not a person or no first name is known.",
    )
    suggested_reply: str = Field(
        description="A polite draft reply; do not claim actions "
        "have been completed unless confirmed"
    )

    # Not part of the model's output schema: filled in by the code with what the agent looked up.
    _steps: list[dict] = PrivateAttr(default_factory=list)

    @property
    def steps(self) -> list[dict]:
        """What the agent checked before deciding, as ``[{"label", "result"}]`` (empty if nothing)."""
        return self._steps
