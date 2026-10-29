"""automation/step4_pipeline.py: sync_drafts() and process_new_emails(), with Gmail/model mocked out."""

import pytest

from agent.schemas import Email, EmailAnalysis, EmailCategory, Urgency
from automation import step3_storage as storage
from automation import step4_pipeline as pipeline


@pytest.fixture(autouse=True)
def clear_gone_once():
    pipeline._gone_once.clear()
    yield
    pipeline._gone_once.clear()


def _save(message_id, subject="Subj", status="drafted"):
    analysis = EmailAnalysis(
        category=EmailCategory.WORK, urgency=Urgency.LOW, summary="s", reason="r",
        requires_action=False, suggested_reply="x",
    )
    storage.save_result(Email(message_id=message_id, sender="a@b.com", subject=subject, body="b"), analysis)
    storage.set_status(message_id, status)


class TestSyncDrafts:
    def test_no_drafted_emails_is_a_noop(self):
        assert pipeline.sync_drafts() == 0

    def test_sent_draft_moves_to_replied(self, monkeypatch):
        _save("m1")
        monkeypatch.setattr(pipeline, "draft_states", lambda rows: {"m1": "sent"})
        changed = pipeline.sync_drafts()
        assert changed == 1
        assert storage.get_email("m1")["status"] == "replied"

    def test_still_a_draft_does_not_change_status(self, monkeypatch):
        _save("m1")
        monkeypatch.setattr(pipeline, "draft_states", lambda rows: {"m1": "draft"})
        changed = pipeline.sync_drafts()
        assert changed == 0
        assert storage.get_email("m1")["status"] == "drafted"

    def test_gone_once_does_not_revert_yet(self, monkeypatch):
        _save("m1")
        monkeypatch.setattr(pipeline, "draft_states", lambda rows: {"m1": "gone"})
        changed = pipeline.sync_drafts()
        assert changed == 0
        assert storage.get_email("m1")["status"] == "drafted"
        assert "m1" in pipeline._gone_once

    def test_gone_twice_in_a_row_reverts_to_new(self, monkeypatch):
        _save("m1")
        monkeypatch.setattr(pipeline, "draft_states", lambda rows: {"m1": "gone"})
        pipeline.sync_drafts()  # first "gone": just remembered
        changed = pipeline.sync_drafts()  # second "gone" in a row: reverts
        assert changed == 1
        assert storage.get_email("m1")["status"] == "new"

    def test_reappearing_as_draft_clears_the_gone_memory(self, monkeypatch):
        _save("m1")
        calls = iter([{"m1": "gone"}, {"m1": "draft"}, {"m1": "gone"}])
        monkeypatch.setattr(pipeline, "draft_states", lambda rows: next(calls))
        pipeline.sync_drafts()  # gone once
        pipeline.sync_drafts()  # draft again: clears the "missing once" memory
        changed = pipeline.sync_drafts()  # gone again: counts as only the first miss
        assert changed == 0
        assert storage.get_email("m1")["status"] == "drafted"

    def test_empty_drafted_set_clears_gone_once(self, monkeypatch):
        _save("m1")
        monkeypatch.setattr(pipeline, "draft_states", lambda rows: {"m1": "gone"})
        pipeline.sync_drafts()
        assert "m1" in pipeline._gone_once
        storage.set_status("m1", "new")  # no longer drafted -> drafted_rows() is empty
        pipeline.sync_drafts()
        assert pipeline._gone_once == set()


class TestProcessNewEmails:
    def test_skips_already_processed_emails(self, monkeypatch):
        _save("m1", status="new")
        monkeypatch.setattr(pipeline, "backfill_dates", lambda: 0)
        monkeypatch.setattr(pipeline, "sync_drafts", lambda: 0)
        monkeypatch.setattr(pipeline, "fetch_emails", lambda **kw: [
            Email(message_id="m1", sender="a@b.com", subject="Subj", body="b")
        ])
        called = []
        monkeypatch.setattr(pipeline, "analyze_email", lambda e: called.append(e) or None)
        result = pipeline.process_new_emails()
        assert result.already == 1
        assert result.new == 0
        assert called == []

    def test_analyzes_and_stores_new_emails(self, monkeypatch):
        monkeypatch.setattr(pipeline, "backfill_dates", lambda: 0)
        monkeypatch.setattr(pipeline, "sync_drafts", lambda: 0)
        email = Email(message_id="new1", sender="a@b.com", subject="Subj", body="b")
        monkeypatch.setattr(pipeline, "fetch_emails", lambda **kw: [email])
        analysis = EmailAnalysis(
            category=EmailCategory.WORK, urgency=Urgency.HIGH, summary="s", reason="r",
            requires_action=True, suggested_reply="x",
        )
        monkeypatch.setattr(pipeline, "analyze_email", lambda e: analysis)
        result = pipeline.process_new_emails()
        assert result.new == 1
        assert result.checked == 1
        assert result.message_ids == ("new1",)
        assert storage.get_email("new1")["urgency"] == "high"

    def test_a_failed_analysis_does_not_block_the_rest(self, monkeypatch):
        monkeypatch.setattr(pipeline, "backfill_dates", lambda: 0)
        monkeypatch.setattr(pipeline, "sync_drafts", lambda: 0)
        ok_email = Email(message_id="ok1", sender="a@b.com", subject="ok", body="b")
        bad_email = Email(message_id="bad1", sender="a@b.com", subject="bad", body="b")
        monkeypatch.setattr(pipeline, "fetch_emails", lambda **kw: [bad_email, ok_email])

        def fake_analyze(email):
            if email.message_id == "bad1":
                raise RuntimeError("model is down")
            return EmailAnalysis(
                category=EmailCategory.WORK, urgency=Urgency.LOW, summary="s", reason="r",
                requires_action=False, suggested_reply="x",
            )
        monkeypatch.setattr(pipeline, "analyze_email", fake_analyze)
        result = pipeline.process_new_emails()
        assert result.failed == 1
        assert result.new == 1
        assert not storage.is_processed("bad1")
        assert storage.is_processed("ok1")

    def test_backfill_and_sync_failures_do_not_block_processing(self, monkeypatch):
        def boom():
            raise RuntimeError("gmail unreachable")
        monkeypatch.setattr(pipeline, "backfill_dates", boom)
        monkeypatch.setattr(pipeline, "sync_drafts", boom)
        monkeypatch.setattr(pipeline, "fetch_emails", lambda **kw: [])
        result = pipeline.process_new_emails()
        assert result.checked == 0
