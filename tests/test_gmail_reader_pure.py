"""automation/step2_gmail_reader.py: the pure, non-network helpers."""

from datetime import date
from email import message_from_string
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from automation.step2_gmail_reader import _decode, _extract_body, _imap_date, _received_at


class TestDecode:
    def test_plain_ascii_passes_through(self):
        assert _decode("Hello World") == "Hello World"

    def test_none_becomes_empty_string(self):
        assert _decode(None) == ""

    def test_decodes_rfc2047_encoded_header(self):
        # "Héllo" encoded per RFC 2047 (UTF-8, base64)
        encoded = "=?utf-8?b?SMOpbGxv?="
        assert _decode(encoded) == "Héllo"


class TestReceivedAt:
    def test_valid_date_header_returns_iso_utc(self):
        msg = message_from_string("Date: Mon, 27 Oct 2026 10:00:00 +0000\n\nbody")
        result = _received_at(msg)
        assert result == "2026-10-27T10:00:00+00:00"

    def test_converts_non_utc_offset_to_utc(self):
        msg = message_from_string("Date: Mon, 27 Oct 2026 10:00:00 +0530\n\nbody")
        result = _received_at(msg)
        assert result == "2026-10-27T04:30:00+00:00"

    def test_missing_date_header_returns_none(self):
        msg = message_from_string("Subject: no date\n\nbody")
        assert _received_at(msg) is None

    def test_malformed_date_header_returns_none(self):
        msg = message_from_string("Date: not a date\n\nbody")
        assert _received_at(msg) is None


class TestExtractBody:
    def test_plain_text_part(self):
        msg = MIMEText("Hello plain text", "plain")
        assert _extract_body(msg).strip() == "Hello plain text"

    def test_falls_back_to_html_with_tags_stripped(self):
        msg = MIMEText("<p>Hello <b>world</b></p>", "html")
        assert _extract_body(msg) == "Hello world"

    def test_prefers_plain_over_html_in_multipart(self):
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText("plain version", "plain"))
        msg.attach(MIMEText("<p>html version</p>", "html"))
        assert _extract_body(msg) == "plain version"

    def test_skips_attachments(self):
        msg = MIMEMultipart()
        body = MIMEText("the real body", "plain")
        msg.attach(body)
        attachment = MIMEText("not the body", "plain")
        attachment.add_header("Content-Disposition", "attachment", filename="notes.txt")
        msg.attach(attachment)
        assert _extract_body(msg) == "the real body"


class TestImapDate:
    def test_format_matches_imap_spec(self):
        assert _imap_date(date(2026, 10, 27)) == "27-Oct-2026"

    def test_pads_single_digit_day(self):
        assert _imap_date(date(2026, 1, 5)) == "05-Jan-2026"
