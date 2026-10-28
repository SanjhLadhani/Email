"""Re-analyze stored inbox emails with the current prompt, profile and model.

Use this after changing the agent (prompt, profile, model) to refresh emails
that were analyzed under the old rules. It is safe to run:

* It checks the Ollama server first and stops before touching anything.
* It copies the database to a timestamped backup file.
* Each email's old analysis is replaced only after its new one succeeds, so a
  failure or a dropped connection never leaves you with less than you had.
* Emails you already drafted or dismissed are left alone.

Note: manual urgency changes made in the dashboard on re-analyzed emails are
overwritten by the new analysis (they stay in the feedback table as examples).

Run::

    python reanalyze.py
"""

import shutil
import sys
import time

import config
import check_ollama
import step3_storage as storage
from step1_email_agent import analyze_email
from step2_gmail_reader import fetch_emails


def main() -> int:
    """Refresh every stored email whose status is still ``new``.

    Returns:
        0 on success (even if some emails failed and kept their old analysis),
        1 if the model server is unreachable so nothing was changed.
    """
    if check_ollama.main() != 0:
        print("\nNothing was changed. Start the Colab notebook, update OLLAMA_URL in .env, and retry.")
        return 1

    backup = config.DB_PATH.with_name(f"triage.backup-{time.strftime('%Y%m%d-%H%M%S')}.db")
    shutil.copy(config.DB_PATH, backup)
    print(f"\nBackup saved: {backup.name}")

    stored = {row["message_id"] for row in storage.list_results(status="new")}
    emails = [e for e in fetch_emails(limit=200, unread_only=True) if e.message_id in stored]
    print(f"Re-analyzing {len(emails)} email(s)...\n")

    done = failed = 0
    for item in emails:
        try:
            analysis = analyze_email(item)
        except Exception as exc:
            failed += 1
            print(f"  kept old analysis for {item.subject[:50]!r}: {exc}")
            continue
        storage.save_result(item, analysis)  # replaces only this email's row
        done += 1
        print(f"  [{analysis.urgency.value:8}] {analysis.category.value:9} {item.subject[:55]}")

    print(f"\nDone: {done} refreshed, {failed} kept as before.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
