"""agent/agent_tools.py: the read-only look-ups and the describe_steps() summarizer."""

from dataclasses import dataclass

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from agent import agent_tools
from agent.agent_tools import TriageContext, describe_steps
from agent.schemas import Email
from automation import step3_storage as storage


@dataclass
class FakeRunContext:
    deps: TriageContext


def make_ctx(allow_lookups=True, sender="a@b.com", message_id="m1"):
    return FakeRunContext(deps=TriageContext(email=Email(sender=sender, subject="s", body="b", message_id=message_id), allow_lookups=allow_lookups))


class TestLookupsOff:
    def test_all_three_tools_return_the_off_message(self):
        ctx = make_ctx(allow_lookups=False)
        assert agent_tools.sender_history(ctx) == agent_tools._OFF
        assert agent_tools.search_my_emails(ctx, "anything") == agent_tools._OFF
        assert agent_tools.did_i_already_reply(ctx, "subject") == agent_tools._OFF


class TestSenderHistory:
    def test_no_history_message(self):
        ctx = make_ctx(sender="nobody@nowhere.com")
        assert agent_tools.sender_history(ctx) == "No earlier emails from this sender."

    def test_lists_earlier_emails(self):
        from agent.schemas import EmailAnalysis, EmailCategory, Urgency
        analysis = EmailAnalysis(
            category=EmailCategory.WORK, urgency=Urgency.HIGH, summary="s", reason="r",
            requires_action=False, suggested_reply="x",
        )
        storage.save_result(Email(message_id="old1", sender="a@b.com", subject="Earlier one", body="x"), analysis)
        ctx = make_ctx(sender="a@b.com", message_id="new1")
        result = agent_tools.sender_history(ctx)
        assert "Earlier one" in result
        assert "high" in result


class TestSearchMyEmails:
    def test_blank_query(self):
        ctx = make_ctx()
        assert agent_tools.search_my_emails(ctx, "   ") == "Give a keyword to search for."

    def test_no_matches(self):
        ctx = make_ctx()
        assert "No earlier emails mention" in agent_tools.search_my_emails(ctx, "zzz")


class TestDidIAlreadyReply:
    def test_reports_no_reply_found(self, monkeypatch):
        monkeypatch.setattr(agent_tools.gmail, "find_sent_reply", lambda subject, **kw: None)
        ctx = make_ctx()
        result = agent_tools.did_i_already_reply(ctx, "Some subject")
        assert "No reply" in result

    def test_reports_reply_found(self, monkeypatch):
        monkeypatch.setattr(agent_tools.gmail, "find_sent_reply", lambda subject, **kw: "2026-01-05T00:00:00+00:00")
        ctx = make_ctx()
        result = agent_tools.did_i_already_reply(ctx, "Some subject")
        assert "2026-01-05" in result

    def test_tool_failure_is_reported_not_raised(self, monkeypatch):
        def boom(subject, **kw):
            raise RuntimeError("imap down")
        monkeypatch.setattr(agent_tools.gmail, "find_sent_reply", boom)
        ctx = make_ctx()
        result = agent_tools.did_i_already_reply(ctx, "Some subject")
        assert "Could not check Sent mail" in result


def _tool_call_message(tool_name, tool_call_id, args):
    return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=args, tool_call_id=tool_call_id)])


def _tool_return_message(tool_name, tool_call_id, content):
    return ModelRequest(parts=[ToolReturnPart(tool_name=tool_name, content=content, tool_call_id=tool_call_id)])


class TestDescribeSteps:
    def test_empty_messages_means_no_steps(self):
        assert describe_steps([]) == []

    def test_a_lookup_call_becomes_one_step(self):
        messages = [
            _tool_call_message("sender_history", "tc1", {}),
            _tool_return_message("sender_history", "tc1", "2 earlier email(s) from this sender or domain:\n- line1\n- line2"),
        ]
        steps = describe_steps(messages)
        assert len(steps) == 1
        assert steps[0]["label"] == "Looked at earlier emails from this sender"
        assert "line1" in steps[0]["result"]

    def test_search_my_emails_label_includes_query(self):
        messages = [
            _tool_call_message("search_my_emails", "tc1", {"query": "invoice"}),
            _tool_return_message("search_my_emails", "tc1", "1 earlier email(s) mention 'invoice':\n- a line"),
        ]
        steps = describe_steps(messages)
        assert "invoice" in steps[0]["label"]

    def test_final_answer_tool_call_is_not_a_step(self):
        # The model's structured final answer also arrives as a ToolCallPart/ToolReturnPart,
        # but its tool name is not in agent_tools.TOOLS, so it must be filtered out.
        messages = [
            _tool_call_message("final_result", "tc1", {}),
            _tool_return_message("final_result", "tc1", "Final result processed."),
        ]
        assert describe_steps(messages) == []

    def test_switched_off_result_produces_no_step(self):
        messages = [
            _tool_call_message("sender_history", "tc1", {}),
            _tool_return_message("sender_history", "tc1", agent_tools._OFF),
        ]
        assert describe_steps(messages) == []

    def test_mixed_real_and_final_steps(self):
        messages = [
            _tool_call_message("sender_history", "tc1", {}),
            _tool_return_message("sender_history", "tc1", "No earlier emails from this sender."),
            _tool_call_message("final_result", "tc2", {}),
            _tool_return_message("final_result", "tc2", "Final result processed."),
        ]
        steps = describe_steps(messages)
        assert len(steps) == 1
        assert steps[0]["label"] == "Looked at earlier emails from this sender"
