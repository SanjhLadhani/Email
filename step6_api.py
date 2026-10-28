"""Step 6 - Web server: JSON API plus the HTML/CSS/JS dashboard.

Serves the static front end from ``static/`` and exposes the earlier steps as
endpoints. It only listens on localhost, and nothing here can send email -
the strongest action is saving a Gmail draft.

Run with::

    python step6_api.py

then open http://127.0.0.1:5000
"""

from datetime import date
from pathlib import Path

from flask import Flask, jsonify, request

import check_ollama
import config
import step3_storage as storage
import user_profile
from schemas import Email, Urgency
from step1_email_agent import rewrite_reply
from step2_gmail_reader import fetch_body
from step4_pipeline import process_new_emails
from step5_drafts import save_draft

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


@app.post("/api/rewrite")
def rewrite_draft():
    """Rewrite a draft according to an instruction; nothing is saved until you press save.

    Body: ``{"message_id": "...", "reply": "<current draft>", "instruction": "say I don't have a Mac"}``.
    Responds with ``{"reply": "<new draft>"}``.
    """
    data = request.get_json(silent=True) or {}
    instruction = str(data.get("instruction", "")).strip()[:500]
    if not instruction:
        return jsonify(error="Type what should change first."), 400
    row = storage.get_email(data.get("message_id", ""))
    if row is None:
        return jsonify(error="Unknown email"), 404
    if check_ollama.main(verbose=False) != 0:
        return jsonify(error=config.NO_MODEL_MESSAGE), 503
    try:
        body = fetch_body(row["message_id"]) or row["summary"]  # summary if the email was deleted
        email = Email(sender=row["sender"], subject=row["subject"], body=body)
        reply = rewrite_reply(email, str(data.get("reply", "")).strip(), instruction)
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

    Body: ``{"message_id": "...", "status": "dismissed" | "new"}``.
    """
    data = request.get_json()
    if data["status"] not in VALID_STATUSES:
        return jsonify(error="Invalid status"), 400
    storage.set_status(data["message_id"], data["status"])
    return jsonify(ok=True)


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
