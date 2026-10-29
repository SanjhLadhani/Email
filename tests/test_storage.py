from agent.schemas import Email, EmailAnalysis, EmailCategory, Urgency
from automation import step3_storage as storage


def make_email(message_id="m1", sender="a@b.com", subject="Subj", received_at=None):
    return Email(message_id=message_id, sender=sender, subject=subject, body="body", received_at=received_at)


def make_analysis(urgency=Urgency.LOW, category=EmailCategory.WORK, requires_action=False,
                   suggested_reply="reply", sender_is_person=True, reply_name=""):
    a = EmailAnalysis(
        category=category, urgency=urgency, summary="sum", reason="why",
        requires_action=requires_action, suggested_reply=suggested_reply,
        sender_is_person=sender_is_person, reply_name=reply_name,
    )
    a._steps = [{"label": "l", "result": "r"}]
    return a


class TestSaveAndRead:
    def test_round_trips_all_fields(self):
        email = make_email(received_at="2026-01-01T00:00:00+00:00")
        analysis = make_analysis(sender_is_person=False, reply_name="Sam")
        storage.save_result(email, analysis)

        row = storage.get_email("m1")
        assert row["sender"] == "a@b.com"
        assert row["subject"] == "Subj"
        assert row["urgency"] == "low"
        assert row["category"] == "work"
        assert row["requires_action"] == 0
        assert row["sender_is_person"] == 0
        assert row["reply_name"] == "Sam"
        assert row["status"] == "new"
        assert row["received_at"] == "2026-01-01T00:00:00+00:00"

    def test_requires_message_id(self):
        email = Email(sender="a@b.com", subject="s", body="b")
        try:
            storage.save_result(email, make_analysis())
            assert False, "expected ValueError"
        except ValueError:
            pass

    def test_insert_or_replace_overwrites(self):
        storage.save_result(make_email(), make_analysis(urgency=Urgency.LOW))
        storage.save_result(make_email(), make_analysis(urgency=Urgency.CRITICAL))
        row = storage.get_email("m1")
        assert row["urgency"] == "critical"

    def test_is_processed(self):
        assert not storage.is_processed("m1")
        storage.save_result(make_email(), make_analysis())
        assert storage.is_processed("m1")

    def test_get_email_missing_returns_none(self):
        assert storage.get_email("nope") is None


class TestListResults:
    def test_orders_by_urgency_then_date(self):
        storage.save_result(make_email("m-low", received_at="2026-01-01T00:00:00+00:00"), make_analysis(urgency=Urgency.LOW))
        storage.save_result(make_email("m-crit", received_at="2026-01-01T00:00:00+00:00"), make_analysis(urgency=Urgency.CRITICAL))
        storage.save_result(make_email("m-high", received_at="2026-01-02T00:00:00+00:00"), make_analysis(urgency=Urgency.HIGH))
        ids = [row["message_id"] for row in storage.list_results()]
        assert ids == ["m-crit", "m-high", "m-low"]

    def test_filters_by_urgency_and_status(self):
        storage.save_result(make_email("m1"), make_analysis(urgency=Urgency.HIGH))
        storage.save_result(make_email("m2"), make_analysis(urgency=Urgency.LOW))
        storage.set_status("m2", "dismissed")

        assert [r["message_id"] for r in storage.list_results(urgency="high")] == ["m1"]
        assert [r["message_id"] for r in storage.list_results(status="dismissed")] == ["m2"]


class TestStatusAndReply:
    def test_set_status(self):
        storage.save_result(make_email(), make_analysis())
        storage.set_status("m1", "dismissed")
        assert storage.get_email("m1")["status"] == "dismissed"

    def test_update_reply(self):
        storage.save_result(make_email(), make_analysis(suggested_reply="old"))
        storage.update_reply("m1", "new reply")
        assert storage.get_email("m1")["suggested_reply"] == "new reply"


class TestHandledAndDrafted:
    def test_handled_ids_includes_dismissed_drafted_replied_only(self):
        for mid, status in [("new1", "new"), ("dis1", "dismissed"), ("draft1", "drafted"), ("rep1", "replied")]:
            storage.save_result(make_email(mid), make_analysis())
            storage.set_status(mid, status)
        assert sorted(storage.handled_ids()) == ["dis1", "draft1", "rep1"]

    def test_handled_ids_excludes_imap_placeholder_ids(self):
        storage.save_result(make_email("imap-123"), make_analysis())
        storage.set_status("imap-123", "dismissed")
        assert storage.handled_ids() == []

    def test_drafted_rows(self):
        storage.save_result(make_email("d1", subject="Subj1"), make_analysis())
        storage.set_status("d1", "drafted")
        storage.save_result(make_email("d2", subject="Subj2"), make_analysis())
        assert storage.drafted_rows() == [("d1", "Subj1")]


class TestReceivedDates:
    def test_missing_and_set(self):
        storage.save_result(make_email("x1"), make_analysis())
        assert storage.missing_received_dates() == ["x1"]
        storage.set_received_at("x1", "2026-02-02T00:00:00+00:00")
        assert storage.missing_received_dates() == []
        assert storage.get_email("x1")["received_at"] == "2026-02-02T00:00:00+00:00"

    def test_excludes_imap_placeholder_ids(self):
        storage.save_result(make_email("imap-1"), make_analysis())
        assert storage.missing_received_dates() == []


class TestFeedback:
    def test_urgency_feedback_updates_and_records(self):
        storage.save_result(make_email("f1", sender="jane@acme.com"), make_analysis(urgency=Urgency.LOW))
        storage.record_urgency_feedback("f1", "critical")
        assert storage.get_email("f1")["urgency"] == "critical"
        examples = storage.feedback_examples("jane@acme.com", "urgency", limit=5)
        assert len(examples) == 1
        assert examples[0]["urgency"] == "critical"

    def test_urgency_feedback_noop_when_unchanged(self):
        storage.save_result(make_email("f2"), make_analysis(urgency=Urgency.LOW))
        storage.record_urgency_feedback("f2", "low")
        assert storage.feedback_examples("a@b.com", "urgency", limit=5) == []

    def test_reply_feedback_records_edited_reply(self):
        storage.save_result(make_email("f3", sender="jane@acme.com"), make_analysis(suggested_reply="original"))
        storage.record_reply_feedback("f3", "a much better reply")
        examples = storage.feedback_examples("jane@acme.com", "reply", limit=5)
        assert len(examples) == 1
        assert examples[0]["reply"] == "a much better reply"

    def test_reply_feedback_noop_when_unchanged_or_blank(self):
        storage.save_result(make_email("f4"), make_analysis(suggested_reply="same"))
        storage.record_reply_feedback("f4", "same")
        storage.record_reply_feedback("f4", "   ")
        assert storage.feedback_examples("a@b.com", "reply", limit=5) == []

    def test_feedback_examples_prefers_same_sender_domain(self):
        storage.save_result(make_email("f5", sender="x@acme.com"), make_analysis())
        storage.record_urgency_feedback("f5", "critical")
        storage.save_result(make_email("f6", sender="y@other.com"), make_analysis())
        storage.record_urgency_feedback("f6", "high")
        rows = storage.feedback_examples("someone@acme.com", "urgency", limit=5)
        assert rows[0]["sender"] == "x@acme.com"


class TestSenderHistoryAndSearch:
    def test_sender_history_matches_same_domain_and_excludes_self(self):
        storage.save_result(make_email("h1", sender="a@acme.com", subject="first"), make_analysis())
        storage.save_result(make_email("h2", sender="b@acme.com", subject="second"), make_analysis())
        rows = storage.sender_history("a@acme.com", exclude_id="h1", limit=5)
        subjects = [r["subject"] for r in rows]
        assert "first" not in subjects
        assert "second" in subjects

    def test_sender_history_empty_for_unparseable_address(self):
        assert storage.sender_history("not-an-email", exclude_id=None) == []

    def test_search_emails_matches_subject_sender_or_summary(self):
        storage.save_result(make_email("s1", subject="Project Phoenix update"), make_analysis())
        rows = storage.search_emails("phoenix", exclude_id=None, limit=5)
        assert len(rows) == 1
        assert rows[0]["message_id"] == "s1"

    def test_search_emails_excludes_self(self):
        storage.save_result(make_email("s2", subject="Project Phoenix update"), make_analysis())
        assert storage.search_emails("phoenix", exclude_id="s2", limit=5) == []
