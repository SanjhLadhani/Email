"""Pure text-processing logic in agent/step1_email_agent.py - no model calls."""

from agent.step1_email_agent import clean_body, compose_reply


class TestCleanBody:
    def test_strips_leading_greeting(self):
        assert clean_body("Hi John,\nThanks for the update.", "", "") == "Thanks for the update."

    def test_strips_sign_off_on_its_own_lines(self):
        body = "Thanks for the update.\n\nBest regards,\nSanjh Ladhani"
        assert clean_body(body, "", "") == "Thanks for the update."

    def test_strips_inline_run_on_sign_off(self):
        body = "Sounds good, see you soon? Best, Sanjh Ladhani"
        assert clean_body(body, "", "") == "Sounds good, see you soon?"

    def test_fills_my_name_placeholder(self):
        body = "Regards, [Your Name]"
        # the sign-off regex eats the trailing "[Your Name]" bracket form only if it looks like a sign-off;
        # test the placeholder substitution directly via a body that keeps it mid-text.
        body2 = "Please contact [Your Name] for details."
        assert "Sanjh" in clean_body(body2, "Sanjh", "")

    def test_fills_their_name_placeholder(self):
        body = "Please let [Sender Name] know."
        assert "Priya" in clean_body(body, "", "Priya")

    def test_capitalizes_first_letter(self):
        assert clean_body("hello there.", "", "") == "Hello there."

    def test_empty_body_stays_empty(self):
        assert clean_body("", "", "") == ""


class TestComposeReply:
    def test_empty_body_returns_empty_string(self):
        assert compose_reply("", "Jane <jane@example.com>") == ""

    def test_uses_explicit_name_over_sender_display_name(self):
        reply = compose_reply("Thanks.", "Someone Else <x@example.com>", name="Priya")
        assert reply.startswith("Hi Priya,")

    def test_empty_name_means_plain_hello(self):
        reply = compose_reply("Thanks.", "x@example.com", name="")
        assert reply.startswith("Hello,")

    def test_none_name_falls_back_to_sender_display_name(self):
        reply = compose_reply("Thanks.", "Jane Doe <jane@example.com>", name=None)
        assert reply.startswith("Hi Jane,")

    def test_none_name_with_no_display_name_falls_back_to_hello(self):
        reply = compose_reply("Thanks.", "jane@example.com", name=None)
        assert reply.startswith("Hello,")

    def test_includes_sign_off(self):
        reply = compose_reply("Thanks.", "jane@example.com", name="Jane")
        assert "Best regards" in reply
        assert "Thanks." in reply
