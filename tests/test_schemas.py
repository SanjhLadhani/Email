from agent.schemas import Email, EmailAnalysis, EmailCategory, RewrittenReply, Urgency, is_noreply


class TestIsNoreply:
    def test_matches_common_noreply_forms(self):
        for sender in [
            "no-reply@example.com",
            "noreply@example.com",
            "DoNotReply@example.com",
            "mailer-daemon@example.com",
            "postmaster@example.com",
            "notifications@github.com",
            "alerts@bank.com",
            "bounce@mailchimp.com",
        ]:
            assert is_noreply(sender), sender

    def test_does_not_flag_a_real_person(self):
        assert not is_noreply("Jane Doe <jane.doe@example.com>")
        assert not is_noreply("manager@company.com")


class TestEmail:
    def test_to_prompt_truncates_long_body(self):
        email = Email(sender="a@b.com", subject="Hi", body="x" * 5000)
        prompt = email.to_prompt()
        # MAX_BODY_CHARS is 4000; the template adds its own text around the body.
        assert "x" * 4000 in prompt
        assert "x" * 4001 not in prompt

    def test_optional_fields_default_to_none(self):
        email = Email(sender="a@b.com", subject="Hi", body="body")
        assert email.message_id is None
        assert email.received_at is None
        assert email.to is None
        assert email.cc is None


class TestRewrittenReply:
    def test_accepts_aliased_field_names(self):
        for alias in ("reply", "body", "text", "message", "content"):
            model = RewrittenReply.model_validate({alias: "hello"})
            assert model.reply == "hello"

    def test_defaults(self):
        model = RewrittenReply.model_validate({"reply": "hi"})
        assert model.reply_name == ""
        assert model.sender_is_person is True


class TestEmailAnalysis:
    def _make(self, **overrides):
        base = dict(
            category=EmailCategory.WORK,
            urgency=Urgency.LOW,
            summary="s",
            reason="r",
            requires_action=False,
            suggested_reply="reply",
        )
        base.update(overrides)
        return EmailAnalysis(**base)

    def test_steps_default_to_empty(self):
        analysis = self._make()
        assert analysis.steps == []

    def test_steps_is_not_part_of_the_output_schema(self):
        # The model must never be asked to produce _steps itself - it is filled in by our code.
        assert "steps" not in EmailAnalysis.model_json_schema()["properties"]
        assert "_steps" not in EmailAnalysis.model_json_schema()["properties"]

    def test_steps_can_be_set_after_construction(self):
        analysis = self._make()
        analysis._steps = [{"label": "x", "result": "y"}]
        assert analysis.steps == [{"label": "x", "result": "y"}]

    def test_sender_is_person_and_reply_name_defaults(self):
        analysis = self._make()
        assert analysis.sender_is_person is True
        assert analysis.reply_name == ""
