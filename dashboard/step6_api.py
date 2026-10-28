"""Step 6 - Web server: JSON API plus the HTML/CSS/JS dashboard.

Serves the static front end from ``static/`` and exposes the earlier steps as
endpoints. It only listens on localhost, and nothing here can send email -
the strongest action is saving a Gmail draft.

Run with::

    python -m dashboard.step6_api

then open http://127.0.0.1:5000
"""

import time
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, request

from automation import check_ollama
import config
from automation import step3_storage as storage
from agent import user_profile
from agent.schemas import Email, Urgency
from agent.step1_email_agent import rewrite_reply
from automation.step2_gmail_reader import count_unread_among, fetch_body, set_seen, unread_counts
from automation.step4_pipeline import process_new_emails, sync_drafts
from automation.step5_drafts import save_draft

app = Flask(__name__, static_folder=str(Path(__file__).parent / "static"), static_url_path="")

VALID_STATUSES = {"new", "dismissed"}
VALID_URGENCIES = {u.value for u in Urgency}


@app.get("/")
def index():
    """Serve the dashboard page."""
    return app.send_static_file("index.html")


@app.get("/api/config")
def get_config():
    """Return the mailbox address and model name for display and Gmail links."""
    found = config.backend()
    return jsonify(
        email=config.EMAIL_ADDRESS,
        name=user_profile.signature_name(),
        model=found.model if found else config.ollama_model(),
    )


_unread = {"at": 0.0, "value": None, "counts": None, "counts_at": 0.0}


def _mirror_in_gmail(message_id: str, seen: bool) -> dict | None:
    """Mark the email read/unread in Gmail and keep the cached unread totals in step with it."""
    try:
        result = set_seen(message_id, seen)
    except Exception:
        return None
    if result and result["changed"] and _unread["counts"]:
        delta = -1 if seen else 1
        _unread["counts"]["all"] = max(_unread["counts"]["all"] + delta, 0)
        if result["primary"]:
            _unread["counts"]["primary"] = max(_unread["counts"]["primary"] + delta, 0)
    return result


@app.get("/api/unread")
def unread():
    """Unread mail still to deal with, two ways. Dismissing marks an email read in Gmail, so it drops out
    of Gmail's own count; an email you only drafted a reply for is still unread there, so it is subtracted
    here. Either way the counts go down as you work.

    * ``unread``: the Primary tab, as Gmail's sidebar counts it (conversations), minus the Primary
      emails you handled here.
    * ``all_left``: every tab together (messages), minus everything you handled here.

    Also returns the raw ``primary_unread`` / ``all_unread`` Gmail totals and how many you handled
    (``handled`` in Primary, ``handled_all`` in any tab). Cached for a minute. ``?fresh=1`` recounts
    everything (about 13 s); ``?quick=1`` reuses the Gmail totals from the last 10 minutes and
    recounts only the emails you handled, which is fast (used right after you dismiss or draft).
    """
    now = time.time()
    quick = bool(request.args.get("quick"))
    if request.args.get("fresh") or quick or now - _unread["at"] > 60:
        try:
            if not (quick and _unread["counts"] and now - _unread["counts_at"] < 600):
                _unread.update(counts=unread_counts(), counts_at=now)
            counts = _unread["counts"]
            ids = storage.handled_ids()
            handled = count_unread_among(ids, primary_only=True)
            handled_all = count_unread_among(ids)
            _unread.update(at=now, value={
                "unread": max(counts["primary"] - handled, 0),
                "all_left": max(counts["all"] - handled_all, 0),
                "primary_unread": counts["primary"],
                "all_unread": counts["all"],
                "handled": handled,
                "handled_all": handled_all,
            })
        except Exception as exc:
            return jsonify(error=str(exc), **(_unread["value"] or {"unread": None})), 503
    return jsonify(_unread["value"])


@app.get("/api/emails")
def list_emails():
    """Return every stored analysis, most urgent first."""
    return jsonify([dict(row) for row in storage.list_results()])


@app.get("/api/status")
def model_status():
    """Report whether the model server is reachable right now."""
    found = config.backend()
    return jsonify(
        online=found is not None,
        model=found.model if found else config.ollama_model(),
        via=found.kind if found else None,
    )


@app.post("/api/fetch")
def fetch_new():
    """Read emails from Gmail, analyze the new ones and store the results.

    Body: ``{"limit": 10, "unread_only": true, "since": "2026-10-26", "until": "2026-10-26"}``.
    ``since``/``until`` (YYYY-MM-DD, your local days) are optional; without them the
    newest emails are taken. Responds with how many new emails were processed.
    """
    body = request.get_json(silent=True) or {}
    limit = int(body.get("limit", 10))
    try:
        since = date.fromisoformat(body["since"]) if body.get("since") else None
        until = date.fromisoformat(body["until"]) if body.get("until") else None
    except ValueError:
        return jsonify(error="Dates must look like 2026-10-26."), 400
    if since and until and until < since:
        return jsonify(error="The end date is before the start date."), 400
    if check_ollama.main(verbose=False) != 0:
        return jsonify(error=config.NO_MODEL_MESSAGE), 503
    try:
        run = process_new_emails(max(1, min(limit, 50)), bool(body.get("unread_only", True)), since, until)
    except Exception as exc:
        return jsonify(error=str(exc)), 500
    return jsonify(run._asdict())


_sync = {"at": 0.0}


@app.get("/api/sync")
def sync():
    """Check Gmail for drafts that were sent or deleted and update their emails.

    Cached for 45 seconds. Responds with ``{"changed": n}``: how many emails moved out of *Drafted*
    (sent ones to *Replied*, deleted ones back to the inbox).
    """
    if time.time() - _sync["at"] < 45:
        return jsonify(changed=0)
    _sync["at"] = time.time()
    try:
        return jsonify(changed=sync_drafts())
    except Exception as exc:
        return jsonify(error=str(exc), changed=0), 503


@app.post("/api/rewrite")
def rewrite_draft():
    """Rewrite a draft according to an instruction, or write one if there is none yet.

    Nothing is saved until you press save. With an empty ``reply`` the instruction is
    optional ("Write a draft" for an email the agent decided needed no reply).

    Body: ``{"message_id": "...", "reply": "<current draft>", "instruction": "say I don't have a Mac"}``.
    Responds with ``{"reply": "<new draft>"}``.
    """
    data = request.get_json(silent=True) or {}
    instruction = str(data.get("instruction", "")).strip()[:500]
    if not instruction and str(data.get("reply", "")).strip():
        return jsonify(error="Type what should change first."), 400
    row = storage.get_email(data.get("message_id", ""))
    if row is None:
        return jsonify(error="Unknown email"), 404
    if check_ollama.main(verbose=False) != 0:
        return jsonify(error=config.NO_MODEL_MESSAGE), 503
    try:
        body = fetch_body(row["message_id"]) or row["summary"]  # summary if the email was deleted
        email = Email(sender=row["sender"], subject=row["subject"], body=body)
        reply = rewrite_reply(
            email, str(data.get("reply", "")).strip(), instruction,
            sender_is_person=bool(row["sender_is_person"]), reply_name=row["reply_name"],
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception as exc:
        return jsonify(error=f"Could not rewrite the draft: {exc}"), 500
    return jsonify(reply=reply)


@app.post("/api/draft")
def make_draft():
    """Save the (possibly edited) reply as a Gmail draft and mark it drafted.

    Body: ``{"message_id": "...", "reply": "..."}``.
    """
    data = request.get_json()
    row = next((r for r in storage.list_results() if r["message_id"] == data["message_id"]), None)
    if row is None:
        return jsonify(error="Unknown email"), 404
    reply = data["reply"].strip()
    if not reply:
        return jsonify(error="Reply is empty"), 400
    try:
        save_draft(row["sender"], row["subject"], reply, row["message_id"])
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception as exc:
        return jsonify(error=f"Could not save draft: {exc}"), 500
    storage.record_reply_feedback(row["message_id"], reply)
    storage.update_reply(row["message_id"], reply)
    storage.set_status(row["message_id"], "drafted")
    return jsonify(ok=True)


@app.post("/api/status")
def change_status():
    """Dismiss or restore an email.

    Body: ``{"message_id": "...", "status": "dismissed" | "new"}``. Dismissing also marks the
    email **read in Gmail** (you have dealt with it); restoring a dismissed email marks it unread
    again. That is the only change MailMind makes to your mailbox besides saving drafts. A failure
    to reach Gmail never blocks the dismiss: the response says whether Gmail was updated.
    """
    data = request.get_json()
    if data["status"] not in VALID_STATUSES:
        return jsonify(error="Invalid status"), 400
    before = storage.get_email(data["message_id"])
    storage.set_status(data["message_id"], data["status"])
    result = None
    if data["status"] == "dismissed" or (before is not None and before["status"] == "dismissed"):
        result = _mirror_in_gmail(data["message_id"], seen=data["status"] == "dismissed")
    return jsonify(ok=True, gmail_marked=bool(result and result["changed"]))


@app.post("/api/urgency")
def change_urgency():
    """Correct an email's urgency; the agent learns from it as a past example.

    Body: ``{"message_id": "...", "urgency": "critical" | "high" | "medium" | "low"}``.
    """
    data = request.get_json()
    if data["urgency"] not in VALID_URGENCIES:
        return jsonify(error="Invalid urgency"), 400
    storage.record_urgency_feedback(data["message_id"], data["urgency"])
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
