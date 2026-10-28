"""Step 1 - The triage agent: Pydantic AI + a local Ollama model.

A Pydantic AI agent turns an ``Email`` (input schema) into a validated
``EmailAnalysis`` (output schema). The agent has no tools and cannot send
anything; it only analyzes text.

It is personalized two ways:

* ``profile.json`` (written by you, trusted) is added to its instructions.
* Your past corrections (urgency changes, edited replies) are shown as
  *untrusted reference examples* in the prompt - never as instructions.

Run directly to try it on a few sample emails::

    python step1_email_agent.py
"""

from pydantic_ai import Agent, NativeOutput, PromptedOutput
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider

import re
from email.utils import parseaddr

import config
import schemas
import tasks
import user_profile
import step3_storage as storage
from schemas import Email, EmailAnalysis, EmailCategory, RewrittenReply, Urgency, is_noreply  # noqa: F401 (re-exported)

def personal_profile() -> str:
    """Add the user's ``profile.json`` to the instructions, re-read per run."""
    return user_profile.render_profile()


_agents: dict[tuple, Agent] = {}


def get_agent(task: str = "analyze") -> Agent:
    """Return the agent for the model that is available *right now*.

    The Colab GPU comes and goes, and a hosted API may stand in for it, so the
    agent is built on demand and cached per backend.

    Args:
        task: ``"analyze"`` (triage an email) or ``"rewrite"`` (rewrite a draft).

    Raises:
        RuntimeError: If neither Colab nor an API key is available.
    """
    found = config.backend()
    if found is None:
        raise RuntimeError(config.NO_MODEL_MESSAGE)
    key = (task, found.kind, found.base_url, found.model, found.api_key)
    if key not in _agents:
        schema, instructions = (
            (EmailAnalysis, [tasks.agent_instructions, personal_profile])
            if task == "analyze"
            else (RewrittenReply, [tasks.rewrite_instructions, personal_profile])
        )
        if found.kind == "colab":
            provider = OllamaProvider(base_url=found.base_url)
            # NativeOutput asks Ollama for schema-constrained JSON, which small
            # local models follow far more reliably than tool-call based output.
            output = NativeOutput(schema)
        else:
            provider = OpenAIProvider(base_url=found.base_url, api_key=found.api_key)
            # Works on any hosted model, whether or not it supports strict JSON schemas.
            output = PromptedOutput(schema)
        _agents[key] = Agent(
            OpenAIChatModel(found.model, provider=provider),
            output_type=output,
            instructions=instructions,
            retries=3,
        )
    return _agents[key]


def _run(task: str, prompt: str):
    """Run the agent for ``task`` once, counting it when a hosted API is in use."""
    found = config.backend()
    if found is not None and found.kind == "api":
        config.count_api_call()
    return get_agent(task).run_sync(prompt).output


def feedback_block(sender: str) -> str:
    """Render the user's past corrections as a reference block for the prompt.

    Same-sender examples come first, then the most recent ones. Returns ''
    when there is no feedback yet. The wording is in ``tasks.yaml``
    (section ``past_decisions``).
    """
    urgency = storage.feedback_examples(sender, "urgency", limit=4)
    replies = storage.feedback_examples(sender, "reply", limit=2)
    if not urgency and not replies:
        return ""
    t = tasks.past_decisions()
    lines = [t["header"]]
    for row in urgency:
        lines.append(
            t["urgency_line"].format(
                sender=row["sender"][:60],
                subject=row["subject"][:80],
                urgency=row["urgency"],
                category=row["category"],
            )
        )
    for row in replies:
        lines.append(t["reply_line"].format(reply=row["reply"][:300]))
    return "\n".join(lines) + "\n\n" + t["email_marker"] + "\n"


# Things small models add despite instructions: their own greeting / sign-off.
_GREETING = re.compile(r"^\s*(hi|hello|hey|dear)\b[^,\n]{0,40}(,\s*|\n+)", re.IGNORECASE)
_SIGN_WORDS = r"(best regards|kind regards|warm regards|regards|sincerely|best wishes|best|cheers|thanks|thank you)"
# On its own line(s): "Best regards,\nName".
_SIGN_OFF = re.compile(
    r"\n+\s*" + _SIGN_WORDS + r"\s*,?\s*(\n[^\n]{0,40}){0,2}\s*$",
    re.IGNORECASE,
)
# Run on after the last sentence: "... soon? Best, Sanjh Ladhani".
_SIGN_OFF_INLINE = re.compile(
    r"(?<=[.!?])\s+" + _SIGN_WORDS + r",\s*[A-Z][\w.' -]{0,40}$",
    re.IGNORECASE,
)
_MY_NAME = re.compile(
    r"[\[<{(]\s*(your|my|recipient'?s?|user'?s?)\s*(full\s*)?name\s*[\]>})]", re.IGNORECASE
)
_THEIR_NAME = re.compile(
    r"[\[<{(]\s*(sender|their|his|her|contact|client|manager)('s)?\s*(full\s*)?name\s*[\]>})]",
    re.IGNORECASE,
)


def clean_body(body: str, my_name: str, their_name: str) -> str:
    """Remove a model-written greeting/sign-off and fill in name placeholders.

    ``[Your Name]``-style placeholders become your real name; ``[Sender Name]``
    becomes the sender's first name. Other placeholders (a missing date, say)
    are left in place so you notice and fill them in before sending.
    """
    body = _GREETING.sub("", body.strip())
    body = _SIGN_OFF.sub("", body)
    body = _SIGN_OFF_INLINE.sub("", body)
    if my_name:
        body = _MY_NAME.sub(my_name, body)
    if their_name:
        body = _THEIR_NAME.sub(their_name, body)
    return body.strip()


def compose_reply(body: str, sender: str, name: str | None = None) -> str:
    """Wrap a reply body in a proper greeting and sign-off.

    Args:
        body: The model's reply text (empty means "no reply needed").
        sender: The original ``From`` header, used to address the greeting when
            ``name`` is not given.
        name: Who to greet, as judged by the model. ``""`` means a plain "Hello,";
            None falls back to the first word of the sender's display name.

    Returns:
        The full reply with "Hi <first name>," on top and "Best regards" plus
        your profile name at the bottom, or "" if the body is empty.
    """
    display = parseaddr(sender)[0].strip().strip('"')
    first = name.strip() if name is not None else (display.split()[0] if display else "")
    name = user_profile.signature_name()
    body = clean_body(body, name, first)
    if not body:
        return ""
    greeting = f"Hi {first}," if first else "Hello,"
    sign_off = f"Best regards,\n{name}" if name else "Best regards"
    return f"{greeting}\n\n{body}\n\n{sign_off}"


def analyze_email(email: Email, use_feedback: bool = True) -> EmailAnalysis:
    """Classify one email and draft a reply.

    Args:
        email: The email to analyze.
        use_feedback: Include the user's past corrections as reference
            examples. Turn off for repeatable evaluations.

    Returns:
        A validated ``EmailAnalysis``. Pydantic AI retries the model if its
        output does not match the schema.
    """
    prompt = email.to_prompt()
    if use_feedback:
        prompt = feedback_block(email.sender) + prompt
    result = _run("analyze", prompt)
    # Hard rule, not left to the model: never draft a reply to a no-reply address.
    if is_noreply(email.sender):
        result.suggested_reply = ""
    else:
        # The model judged whether a real person wrote this; if not, nobody is waiting for a reply.
        result.suggested_reply = (
            compose_reply(result.suggested_reply, email.sender, result.reply_name)
            if result.sender_is_person
            else ""
        )
    return result


def rewrite_reply(email: Email, current_reply: str, instruction: str) -> str:
    """Rewrite a draft reply according to what the user asked for.

    Args:
        email: The email being answered (its body gives the model the context).
        current_reply: The draft as it is now (greeting and sign-off included).
        instruction: What to change, in the user's words, e.g. "say I don't have a Mac".

    Returns:
        The new full reply, with greeting and sign-off.

    Raises:
        ValueError: For a no-reply sender or an empty result.
    """
    if is_noreply(email.sender):
        raise ValueError("This is an automated address that cannot receive replies.")
    prompt = tasks.rewrite_prompt(
        email.sender, email.subject, email.body[: schemas.MAX_BODY_CHARS], current_reply, instruction
    )
    # Keep whoever the current draft already greets ("Hello," stays "Hello,").
    greeted = re.match(r"\s*(?:hi|hello|hey|dear)\s+([^,\n]+),", current_reply, re.IGNORECASE)
    reply = compose_reply(_run("rewrite", prompt).reply, email.sender, greeted.group(1) if greeted else "")
    if not reply:
        raise ValueError("The model returned an empty reply. Try rewording the instruction.")
    return reply


SAMPLE_EMAILS = [
    Email(
        sender="manager@company.com",
        subject="Project report needed today",
        body="Hi, the client review is at 4 PM. Please send me the final "
        "project report before 2 PM so I can go through it.",
    ),
    Email(
        sender="security@bank-alerts.example",
        subject="Unusual sign-in detected",
        body="We noticed a sign-in to your account from a new device in "
        "another country. If this wasn't you, reset your password immediately.",
    ),
    Email(
        sender="deals@shop.example",
        subject="URGENT: 50% off ends tonight!",
        body="Don't miss our biggest sale of the year. Shop now!",
    ),
]


if __name__ == "__main__":
    for sample in SAMPLE_EMAILS:
        print(f"--- {sample.subject} ---")
        print(analyze_email(sample, use_feedback=False).model_dump_json(indent=2))
