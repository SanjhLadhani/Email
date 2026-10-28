"""Make autopilot start by itself every time you log in to Windows.

It puts one small launcher in your Startup folder. The launcher runs
``autopilot.py`` hidden (no console window) and writes its output to
``autopilot.log`` (restarted at each login). No admin rights needed.

Run::

    python -m automation.install_autostart            # turn automatic start on (and start it now)
    python -m automation.install_autostart --remove   # turn it off again
    python -m automation.install_autostart --status   # is it installed / running?

You still have to run the Colab notebook yourself - Colab needs your Google
sign-in. Until then the dashboard shows "Model offline" and autopilot waits.
"""

import argparse
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent  # the project root, where .env and the database live
STARTUP = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
LAUNCHER = STARTUP / "EmailTriageAutopilot.vbs"
DASHBOARD = "http://127.0.0.1:5000/api/status"


def launcher_script() -> str:
    """Build the VBScript that starts autopilot hidden, logging to autopilot.log."""
    python = PROJECT / ".venv" / "Scripts" / "python.exe"
    command = f'cmd /c ""{python}" -u -m automation.autopilot > autopilot.log 2>&1"'
    escaped = command.replace('"', '""')  # VBScript escapes a quote by doubling it
    return (
        'Set shell = CreateObject("WScript.Shell")\n'
        f'shell.CurrentDirectory = "{PROJECT}"\n'
        f'shell.Run "{escaped}", 0, False\n'
    )


def is_running() -> bool:
    """True if the dashboard server answers."""
    try:
        urllib.request.urlopen(DASHBOARD, timeout=3)
        return True
    except Exception:
        return False


def main() -> int:
    """Install, remove or report the automatic-start launcher."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--remove", action="store_true", help="turn automatic start off")
    group.add_argument("--status", action="store_true", help="show whether it is installed and running")
    args = parser.parse_args()

    if args.status:
        print(f"Starts at login: {'yes' if LAUNCHER.exists() else 'no'}")
        print(f"Running now:     {'yes' if is_running() else 'no'}")
        return 0

    if args.remove:
        LAUNCHER.unlink(missing_ok=True)
        print("Automatic start removed. (If autopilot is running now, close it with Ctrl+C or restart Windows.)")
        return 0

    STARTUP.mkdir(parents=True, exist_ok=True)
    LAUNCHER.write_text(launcher_script(), encoding="utf-8")
    print(f"Installed: {LAUNCHER}")
    if is_running():
        print("Autopilot is already running.")
    else:
        subprocess.Popen(["wscript.exe", str(LAUNCHER)])
        print("Started it now. Dashboard: http://127.0.0.1:5000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
