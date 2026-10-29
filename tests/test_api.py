"""dashboard/step6_api.py: the Flask JSON API, with Gmail/model/storage mocked out.

Uses the real storage module against the per-test SQLite file from conftest.py, but
mocks every Gmail and model call so the suite stays network-free.
"""

import pytest

import config
from agent.schemas import Email, EmailAnalysis, EmailCategory, Urgency
from automation import step3_storage as storage
from dashboard import step6_api as api


@pytest.fixture
def client():
    api.app.testing = True
    api._unread.update(at=0.0, value=None, counts=None, counts_at=0.0)
    api._sync.update(at=0.0)
    return api.app.test_client()


def _seed_email(message_id="m1", status="new", sender="a@b.com", subject="Subj", sender_is_person=True):
    analysis = EmailAnalysis(
        category=EmailCategory.WORK, urgency=Urgency.HIGH, summary="sum", reason="why",
        requires_action=True, suggested_reply="Hi,\n\nThanks.\n\nBest regards,",
        sender_is_person=sender_is_person, reply_name="",
    )
    storage.save_result(Email(message_id=message_id, sender=sender, subject=subject, body="b"), analysis)
    storage.set_status(message_id, status)


class TestConfigEndpoint:
    def test_reports_configured_model(self, client, monkeypatch):
        monkeypatch.setattr(api.config, "backend", lambda: config.Backend("api", "https://x", "gemini-x", "k"))
        monkeypatch.setattr(api.user_profile, "signature_name", lambda: "Sanjh")
        resp = client.get("/api/config")
        assert resp.status_code == 200
        assert resp.json["model"] == "gemini-x"
        assert resp.json["name"] == "Sanjh"

    def test_falls_back_to_ollama_model_when_no_backend(self, client, monkeypatch):
        monkeypatch.setattr(api.config, "backend", lambda: None)
        monkeypatch.setattr(api.config, "ollama_model", lambda: "qwen3:8b")
        resp = client.get("/api/config")
        assert resp.json["model"] == "qwen3:8b"


class TestEmailsEndpoint:
    def test_lists_stored_emails(self, client):
        _seed_email("m1")
        resp = client.get("/api/emails")
        assert resp.status_code == 200
        assert len(resp.json) == 1
        assert resp.json[0]["message_id"] == "m1"

    def test_empty_when_nothing_stored(self, client):
        assert client.get("/api/emails").json == []


class TestStatusChange:
    def test_rejects_invalid_status(self, client):
        resp = client.post("/api/status", json={"message_id": "m1", "status": "bogus"})
        assert resp.status_code == 400

    def test_dismiss_marks_read_in_gmail(self, client, monkeypatch):
        _seed_email("m1", status="new")
        monkeypatch.setattr(api, "set_seen", lambda mid, seen: {"changed": True, "primary": True})
        resp = client.post("/api/status", json={"message_id": "m1", "status": "dismissed"})
        assert resp.status_code == 200
        assert resp.json["gmail_marked"] is True
        assert storage.get_email("m1")["status"] == "dismissed"

    def test_restore_marks_unread_in_gmail(self, client, monkeypatch):
        _seed_email("m1", status="dismissed")
        calls = []
        monkeypatch.setattr(api, "set_seen", lambda mid, seen: calls.append(seen) or {"changed": True, "primary": False})
        resp = client.post("/api/status", json={"message_id": "m1", "status": "new"})
        assert resp.json["gmail_marked"] is True
        assert calls == [False]

    def test_gmail_failure_does_not_block_the_dismiss(self, client, monkeypatch):
        _seed_email("m1", status="new")
        monkeypatch.setattr(api, "set_seen", lambda mid, seen: (_ for _ in ()).throw(RuntimeError("imap down")))
        resp = client.post("/api/status", json={"message_id": "m1", "status": "dismissed"})
        assert resp.status_code == 200
        assert resp.json["ok"] is True
        assert resp.json["gmail_marked"] is False
        assert storage.get_email("m1")["status"] == "dismissed"


class TestUrgencyChange:
    def test_rejects_invalid_urgency(self, client):
        resp = client.post("/api/urgency", json={"message_id": "m1", "urgency": "nonsense"})
        assert resp.status_code == 400

    def test_records_valid_urgency_correction(self, client):
        _seed_email("m1")
        resp = client.post("/api/urgency", json={"message_id": "m1", "urgency": "critical"})
        assert resp.status_code == 200
        assert storage.get_email("m1")["urgency"] == "critical"


class TestDraftEndpoint:
    def test_unknown_email_is_404(self, client):
        resp = client.post("/api/draft", json={"message_id": "nope", "reply": "hi"})
        assert resp.status_code == 404

    def test_empty_reply_is_rejected(self, client):
        _seed_email("m1")
        resp = client.post("/api/draft", json={"message_id": "m1", "reply": "   "})
        assert resp.status_code == 400

    def test_saves_draft_and_marks_drafted(self, client, monkeypatch):
        _seed_email("m1")
        monkeypatch.setattr(api, "save_draft", lambda *a, **kw: None)
        resp = client.post("/api/draft", json={"message_id": "m1", "reply": "My reply"})
        assert resp.status_code == 200
        row = storage.get_email("m1")
        assert row["status"] == "drafted"
        assert row["suggested_reply"] == "My reply"

    def test_noreply_sender_is_rejected(self, client, monkeypatch):
        _seed_email("m1", sender="no-reply@example.com")

        def raise_value_error(*a, **kw):
            raise ValueError("This sender does not accept replies (no-reply address).")
        monkeypatch.setattr(api, "save_draft", raise_value_error)
        resp = client.post("/api/draft", json={"message_id": "m1", "reply": "hi"})
        assert resp.status_code == 400


class TestFetchEndpoint:
    def test_rejects_malformed_dates(self, client, monkeypatch):
        monkeypatch.setattr(api.check_ollama, "main", lambda verbose=False: 0)
        resp = client.post("/api/fetch", json={"since": "not-a-date"})
        assert resp.status_code == 400

    def test_rejects_end_before_start(self, client, monkeypatch):
        monkeypatch.setattr(api.check_ollama, "main", lambda verbose=False: 0)
        resp = client.post("/api/fetch", json={"since": "2026-02-01", "until": "2026-01-01"})
        assert resp.status_code == 400

    def test_503_when_no_model_available(self, client, monkeypatch):
        monkeypatch.setattr(api.check_ollama, "main", lambda verbose=False: 1)
        resp = client.post("/api/fetch", json={})
        assert resp.status_code == 503

    def test_runs_the_pipeline_when_model_is_up(self, client, monkeypatch):
        monkeypatch.setattr(api.check_ollama, "main", lambda verbose=False: 0)

        class FakeResult:
            def _asdict(self):
                return {"checked": 1, "new": 1, "already": 0, "failed": 0, "message_ids": ("m1",)}
        monkeypatch.setattr(api, "process_new_emails", lambda limit, unread_only, since, until: FakeResult())
        resp = client.post("/api/fetch", json={"limit": 5})
        assert resp.status_code == 200
        assert resp.json["new"] == 1


class TestSyncEndpoint:
    def test_calls_sync_drafts(self, client, monkeypatch):
        monkeypatch.setattr(api, "sync_drafts", lambda: 2)
        resp = client.get("/api/sync")
        assert resp.json["changed"] == 2

    def test_cached_within_window(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr(api, "sync_drafts", lambda: calls.append(1) or 1)
        client.get("/api/sync")
        resp = client.get("/api/sync")
        assert resp.json["changed"] == 0
        assert len(calls) == 1


class TestRewriteEndpoint:
    def test_unknown_email_is_404(self, client):
        resp = client.post("/api/rewrite", json={"message_id": "nope", "reply": "x", "instruction": "y"})
        assert resp.status_code == 404

    def test_requires_instruction_when_reply_is_not_empty(self, client):
        _seed_email("m1")
        resp = client.post("/api/rewrite", json={"message_id": "m1", "reply": "existing draft", "instruction": ""})
        assert resp.status_code == 400

    def test_empty_reply_allows_empty_instruction(self, client, monkeypatch):
        _seed_email("m1")
        monkeypatch.setattr(api.check_ollama, "main", lambda verbose=False: 0)
        monkeypatch.setattr(api, "fetch_body", lambda mid: "the original body")
        monkeypatch.setattr(api, "rewrite_reply", lambda *a, **kw: "Hi,\n\nNew reply.\n\nBest regards,")
        resp = client.post("/api/rewrite", json={"message_id": "m1", "reply": "", "instruction": ""})
        assert resp.status_code == 200
        assert "New reply" in resp.json["reply"]

    def test_503_when_no_model_available(self, client, monkeypatch):
        _seed_email("m1")
        monkeypatch.setattr(api.check_ollama, "main", lambda verbose=False: 1)
        resp = client.post("/api/rewrite", json={"message_id": "m1", "reply": "x", "instruction": "y"})
        assert resp.status_code == 503

    def test_value_error_from_model_becomes_400(self, client, monkeypatch):
        _seed_email("m1")
        monkeypatch.setattr(api.check_ollama, "main", lambda verbose=False: 0)
        monkeypatch.setattr(api, "fetch_body", lambda mid: "body")

        def raise_value_error(*a, **kw):
            raise ValueError("The model returned an empty reply.")
        monkeypatch.setattr(api, "rewrite_reply", raise_value_error)
        resp = client.post("/api/rewrite", json={"message_id": "m1", "reply": "x", "instruction": "y"})
        assert resp.status_code == 400


class TestModelStatusEndpoint:
    def test_reports_online_when_backend_found(self, client, monkeypatch):
        monkeypatch.setattr(api.config, "backend", lambda: config.Backend("colab", "https://x", "qwen3:8b", None))
        resp = client.get("/api/status")
        assert resp.json["online"] is True
        assert resp.json["via"] == "colab"

    def test_reports_offline_when_no_backend(self, client, monkeypatch):
        monkeypatch.setattr(api.config, "backend", lambda: None)
        monkeypatch.setattr(api.config, "ollama_model", lambda: "qwen3:8b")
        resp = client.get("/api/status")
        assert resp.json["online"] is False
        assert resp.json["via"] is None


class TestUnreadEndpoint:
    def test_computes_unread_minus_handled(self, client, monkeypatch):
        _seed_email("m1", status="dismissed")
        monkeypatch.setattr(api, "unread_counts", lambda: {"all": 100, "primary": 40})
        monkeypatch.setattr(api, "count_unread_among", lambda ids, primary_only=False: 1 if ids else 0)
        resp = client.get("/api/unread?fresh=1")
        assert resp.status_code == 200
        assert resp.json["primary_unread"] == 40
        assert resp.json["all_unread"] == 100
        assert resp.json["unread"] == 39
        assert resp.json["all_left"] == 99

    def test_failure_returns_503_with_last_known_value(self, client, monkeypatch):
        def boom():
            raise RuntimeError("imap down")
        monkeypatch.setattr(api, "unread_counts", boom)
        resp = client.get("/api/unread?fresh=1")
        assert resp.status_code == 503
