"""Autopilot: one command that runs the whole triage agent hands-free.

It starts the dashboard, waits for the model server (the Colab notebook
announces its own URL, so nothing needs copying), then checks Gmail every few
minutes and analyzes new mail. If the model server disappears (Colab sessions
end), it waits for the next one and carries on - no restart needed.

Run::

    python autopilot.py                 # dashboard + check mail every 5 minutes
    python autopilot.py --every 2       # check every 2 minutes
    python autopilot.py --reanalyze     # first refresh stored emails with the current prompt
    python autopilot.py --no-dashboard  # just the mail loop

Stop with Ctrl+C.
"""

import argparse
import socket
import sys
import threading
import time

import check_ollama
import config
import reanalyze
from step4_pipeline import process_new_emails


LOCK_PORT = 50123  # nothing else uses it; binding it is how we detect a running copy
_lock_socket: socket.socket | None = None


def already_running() -> bool:
    """Claim the single-instance lock; True if another autopilot already holds it.

    Windows lets two programs share a web port, so the dashboard port cannot
    tell us this. A plain bind on a private local port does: it fails if
    another copy holds it, and the lock vanishes automatically when we exit.
    """
    global _lock_socket
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        lock.close()
        return True
    _lock_socket = lock  # keep it open for the life of the process
    return False


def start_dashboard(port: int) -> None:
    """Serve the dashboard in a background thread (http://127.0.0.1:<port>)."""
    from werkzeug.serving import make_server

    from step6_api import app

    server = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Dashboard: http://127.0.0.1:{port}")


def wait_for_model(poll_seconds: int = 15) -> None:
    """Block until the model server answers, re-discovering its URL each time."""
    announced = False
    while True:
        config.refresh()
        if check_ollama.main(verbose=not announced) == 0:
            return
        if not announced:
            print("\nNo model available (Colab off, no API key). Waiting - this resumes by itself once one is.\n")
            announced = True
        time.sleep(poll_seconds)


def main() -> None:
    """Run the dashboard and the mail-checking loop until interrupted."""
    # Output goes to a log file in the Windows code page, which cannot print emoji
    # in email subjects; without this a single emoji aborts the whole check.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--every", type=float, default=5, help="minutes between mail checks")
    parser.add_argument("--limit", type=int, default=50, help="newest unread emails to consider per check")
    parser.add_argument("--port", type=int, default=5000, help="dashboard port")
    parser.add_argument("--no-dashboard", action="store_true")
    parser.add_argument("--reanalyze", action="store_true", help="refresh stored emails first")
    args = parser.parse_args()

    if already_running():
        print("Autopilot is already running (dashboard: http://127.0.0.1:5000). Exiting.")
        return
    if not args.no_dashboard:
        start_dashboard(args.port)

    wait_for_model()
    if args.reanalyze:
        reanalyze.main()

    while True:
        stamp = time.strftime("%H:%M:%S")
        try:
            wait_for_model()  # the Colab session may have ended since last time
            print(f"[{stamp}] checking Gmail...")
            run = process_new_emails(limit=args.limit)
            print(
                f"[{stamp}] checked {run.checked}: {run.new} new, {run.already} already done, "
                f"{run.failed} failed. Next check in {args.every:g} min."
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"[{stamp}] check failed ({exc}); will retry.")
        time.sleep(args.every * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
