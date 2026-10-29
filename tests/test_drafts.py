"""automation/step5_drafts.py: building the draft message (no IMAP calls)."""

import pytest

import config
from automation.step5_drafts import build_draft


@pytest.fixture(autouse=True)
def fake_credentials(monkeypatch):
    """build_draft() needs EMAIL_ADDRESS/EMAIL_APP_PASSWORD; tests must not depend on the real .env."""
    monkeypatch.setattr(config, "EMAIL_ADDRESS", "me@example.com")
    monkeypatch.setattr(config, "EMAIL_APP_PASSWORD", "fake-app-password")


class TestBuildDraft:
    def test_raises_for_noreply_sender(self):
        with pytest.raises(ValueError):
            build_draft("no-reply@example.com", "Subject", "body")

    def test_raises_for_sender_with_no_address(self):
        with pytest.raises(ValueError):
            build_draft("", "Subject", "body")

    def test_adds_re_prefix_when_missing(self):
        msg = build_draft("jane@example.com", "Project update", "Thanks!")
        assert msg["Subject"] == "Re: Project update"

    def test_does_not_double_prefix(self):
        msg = build_draft("jane@example.com", "Re: Project update", "Thanks!")
        assert msg["Subject"] == "Re: Project update"

    def test_sets_in_reply_to_and_references_when_message_id_given(self):
        msg = build_draft("jane@example.com", "Subject", "body", message_id="<abc@mail>")
        assert msg["In-Reply-To"] == "<abc@mail>"
        assert msg["References"] == "<abc@mail>"

    def test_omits_in_reply_to_when_no_message_id(self):
        msg = build_draft("jane@example.com", "Subject", "body")
        assert msg["In-Reply-To"] is None

    def test_to_header_preserves_display_name(self):
        msg = build_draft("Jane Doe <jane@example.com>", "Subject", "body")
        assert "Jane Doe" in msg["To"]
        assert "jane@example.com" in msg["To"]

    def test_body_is_trimmed(self):
        msg = build_draft("jane@example.com", "Subject", "  hello  \n\n")
        payload = msg.get_content()
        assert payload.strip() == "hello"
