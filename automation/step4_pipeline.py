"""Step 4 - The pipeline: fetch -> skip already-seen -> analyze -> store.

Ties steps 1-3 together. Run it on its own (or on a schedule) to keep the
database up to date::

    python -m automation.step4_pipeline
"""

from datetime import date
from typing import NamedTuple

from agent.step1_email_agent import analyze_email
from automation.step2_gmail_reader import fetch_emails, fetch_received_dates
from automation.step5_drafts import draft_states
from automation.step3_storage import (
    drafted_rows,
    is_processed,
    missing_received_dates,
    save_result,
    set_received_at,
    set_status,
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


_gone_once: set[str] = set()  # drafts missing from Gmail on the previous check


def sync_drafts() -> int:
    """Move emails out of *Drafted* once Gmail says what happened to their draft.

    A reply found in Sent Mail becomes ``replied``. A draft that has vanished without a sent reply
    (you deleted it) goes back to the inbox as ``new``; that is only done when it is missing on two
    checks in a row, so a draft Gmail has not indexed yet is never lost. Best effort: any failure
    leaves everything as it was.

    Returns:
        How many emails changed.
    """
    rows = drafted_rows()
    if not rows:
        _gone_once.clear()
        return 0
    changed = 0
    for message_id, state in draft_states(rows).items():
        if state == "sent":
            set_status(message_id, "replied")
            changed += 1
        elif state == "gone":
            if message_id in _gone_once:
                set_status(message_id, "new")
                changed += 1
            _gone_once.symmetric_difference_update({message_id})
        else:
            _gone_once.discard(message_id)
    return changed


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
    try:
        sync_drafts()
    except Exception as exc:  # the draft check is a nicety too
        print(f"Draft check skipped: {exc}")
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
