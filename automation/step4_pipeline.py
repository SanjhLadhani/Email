"""Step 4 - The pipeline: fetch -> skip already-seen -> analyze -> store.

Ties steps 1-3 together. Run it on its own (or on a schedule) to keep the
database up to date::

    python -m automation.step4_pipeline
"""

from datetime import date
from typing import NamedTuple

from agent.step1_email_agent import analyze_email
from automation.step2_gmail_reader import fetch_emails, fetch_received_dates
from automation.step3_storage import (
    is_processed,
    missing_received_dates,
    save_result,
    set_received_at,
)


def backfill_dates() -> int:
    """Fill in the sent date of stored emails that were saved without one.

    Best effort: emails no longer in the inbox keep no date (the dashboard
    then falls back to the time they were analyzed).

    Returns:
        How many emails got a date.
    """
    missing = missing_received_dates()
    found = fetch_received_dates(missing) if missing else {}
    for message_id, when in found.items():
        set_received_at(message_id, when)
    return len(found)


class RunResult(NamedTuple):
    """What one pass over the inbox did."""

    checked: int  # emails looked at
    new: int  # newly analyzed and stored
    already: int  # skipped: analyzed on an earlier run
    failed: int  # could not be analyzed (kept for the next run)
    message_ids: tuple = ()  # every email looked at, newest first (the dashboard shows just these)


def process_new_emails(
    limit: int = 10,
    unread_only: bool = True,
    since: date | None = None,
    until: date | None = None,
) -> RunResult:
    """Analyze emails that have not been processed before.

    A failure on one email is reported and skipped so it does not block the
    rest; it will be retried on the next run because nothing is stored.

    Args:
        limit: How many of the newest matching emails to consider.
        unread_only: Only look at unread emails.
        since, until: Only emails from these local days (inclusive), e.g. both
            set to yesterday to catch up on a day you missed.

    Returns:
        A ``RunResult`` with how many were checked, new, already done and failed.
    """
    try:
        backfill_dates()
    except Exception as exc:  # dates are a nicety; never block triage
        print(f"Date backfill skipped: {exc}")
    emails = fetch_emails(limit=limit, unread_only=unread_only, since=since, until=until)
    new = already = failed = 0
    for item in emails:
        if is_processed(item.message_id):
            already += 1
            continue
        try:
            analysis = analyze_email(item)
        except Exception as exc:  # model/network errors: retry next run
            print(f"Skipped {item.subject!r}: {exc}")
            failed += 1
            continue
        save_result(item, analysis)
        flag = "!! " if analysis.urgency.value == "critical" else ""
        print(f"{flag}[{analysis.urgency.value}] {item.subject}")
        new += 1
    return RunResult(len(emails), new, already, failed, tuple(e.message_id for e in emails))


if __name__ == "__main__":
    print(process_new_emails())
