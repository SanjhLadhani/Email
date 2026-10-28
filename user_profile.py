"""Your personal profile: tells the agent what matters to *you*.

Edit ``profile.json`` (no restart needed - it is re-read on every email).
Empty fields are ignored. Because you write this file yourself, it is trusted
and goes into the agent's instructions, unlike email content or past examples.

Keys:
    name:                your full name, used to sign replies and in Gmail's From field.
    about:               one or two sentences about you and your work.
    vip_senders:         names, addresses or domains that always deserve attention.
    low_priority_senders: names, addresses or domains that are almost never urgent.
    urgent_means:        what "urgent" means in your life, in your own words.
    reply_style:         how your replies should sound.
"""

import json
from pathlib import Path

import tasks

PROFILE_PATH = Path(__file__).parent / "profile.json"


def load_profile() -> dict:
    """Read ``profile.json``, returning an empty profile if it is missing or invalid."""
    try:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def signature_name() -> str:
    """Return the name used to sign replies ('' if not set)."""
    return str(load_profile().get("name", "")).strip()


def render_profile() -> str:
    """Turn the profile into instruction text for the agent ('' if it is empty).

    The wording of each line is in ``tasks.yaml`` (section ``profile``).
    """
    p = load_profile()
    t = tasks.profile_templates()
    lines = []
    if p.get("name"):
        lines.append(t["name"].format(name=p["name"]))
    if p.get("about"):
        lines.append(t["about"].format(about=p["about"]))
    if p.get("vip_senders"):
        lines.append(t["vip_senders"].format(senders=", ".join(p["vip_senders"])))
    if p.get("low_priority_senders"):
        lines.append(t["low_priority_senders"].format(senders=", ".join(p["low_priority_senders"])))
    if p.get("urgent_means"):
        lines.append(t["urgent_means"].format(urgent_means=p["urgent_means"]))
    if p.get("reply_style"):
        lines.append(t["reply_style"].format(reply_style=p["reply_style"]))
    return t["intro"] + "\n" + "\n".join(lines) if lines else ""
