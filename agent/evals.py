"""Evaluate the triage agent against labelled emails in ``evals/cases.json``.

Run it after changing the prompt, the profile or the model, so you can see
whether quality went up or down instead of guessing::

    python -m agent.evals            # run every case
    python -m agent.evals --limit 5  # just the first five

Add your own cases to ``evals/cases.json`` (real emails you have labelled are
the most valuable). Each case has ``sender``, ``subject``, ``body`` and any of:

* ``urgency``: list of acceptable urgencies
* ``category``: list of acceptable categories
* ``requires_action``: expected true/false
* ``forbid_in_reply``: phrases the draft reply must not contain
* ``reply_empty``: true if no reply should be drafted at all
* ``reply_contains_any``: words, at least one of which the reply must contain
* ``to`` / ``cc``: who the email was addressed to
* ``profile``: a profile (e.g. ``{"vip_senders": [...]}``) to use for this case only

Every reply is also checked for claiming an action was already done, which
the agent must never do.
"""

import argparse
import json
import re
from pathlib import Path

from agent import user_profile
from agent.schemas import Email
from agent.step1_email_agent import analyze_email

CASES_PATH = Path(__file__).parent / "evals" / "cases.json"

# "I have reset ...", "I've already sent ..." etc. - actions the user has not done yet.
FALSE_CLAIM = re.compile(
    r"\b(i have|i've|i had|we have|we've)\s+(already\s+)?"
    r"(reset|checked|sent|paid|fixed|completed|resolved|changed|updated|reported|blocked|secured)\b",
    re.IGNORECASE,
)
LEVELS = ["low", "medium", "high", "critical"]


def check_case(case: dict, result) -> list[str]:
    """Return a list of problems with the agent's result for one case (empty = pass)."""
    problems = []
    if "urgency" in case and result.urgency.value not in case["urgency"]:
        problems.append(f"urgency {result.urgency.value!r}, wanted {case['urgency']}")
    if "category" in case and result.category.value not in case["category"]:
        problems.append(f"category {result.category.value!r}, wanted {case['category']}")
    if "requires_action" in case and result.requires_action != case["requires_action"]:
        problems.append(f"requires_action {result.requires_action}, wanted {case['requires_action']}")
    if FALSE_CLAIM.search(result.suggested_reply):
        problems.append("reply claims an action was already done")
    if case.get("reply_empty") and result.suggested_reply.strip():
        problems.append("drafted a reply where none is needed")
    wanted = case.get("reply_contains_any")
    if wanted and not any(w.lower() in result.suggested_reply.lower() for w in wanted):
        problems.append(f"reply should contain one of {wanted} (language / content check)")
    for phrase in case.get("forbid_in_reply", []):
        if phrase.lower() in result.suggested_reply.lower():
            problems.append(f"reply contains {phrase!r}")
    return problems


def main() -> None:
    """Run all cases and print a per-case result plus a summary."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, help="only run the first N cases")
    args = parser.parse_args()

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))[: args.limit]
    passed = 0
    for case in cases:
        email = Email(
            sender=case["sender"], subject=case["subject"], body=case["body"],
            to=case.get("to"), cc=case.get("cc"),
        )
        real_profile = user_profile.load_profile
        if "profile" in case:  # use this case's profile instead of your real one
            user_profile.load_profile = lambda profile=case["profile"]: profile
        try:
            # Feedback off so scores don't drift as you use the dashboard.
            result = analyze_email(email, use_feedback=False)
            problems = check_case(case, result)
        except Exception as exc:
            problems = [f"agent error: {exc}"]
        finally:
            user_profile.load_profile = real_profile
        passed += not problems
        print(f"{'PASS' if not problems else 'FAIL'}  {case['name']}")
        for problem in problems:
            print(f"        - {problem}")
    print(f"\n{passed}/{len(cases)} cases passed ({100 * passed // max(len(cases), 1)}%)")


if __name__ == "__main__":
    main()
