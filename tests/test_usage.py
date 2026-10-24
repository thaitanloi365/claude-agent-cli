"""Tests for usage ledger and spend calculations."""

import pytest

from claude_agent_cli.usage import (
    QueryUsage,
    UsageLedger,
    get_month_spent,
    get_token_totals,
    get_total_spent,
    load_ledger,
    record_usage,
    save_ledger,
)


def test_month_spent_ignores_earlier_months(tmp_path, monkeypatch):
    monkeypatch.setattr("claude_agent_cli.usage.USAGE_FILE", tmp_path / "usage.json")
    monkeypatch.setattr("claude_agent_cli.usage.DEFAULT_CONFIG_DIR", tmp_path)

    save_ledger(UsageLedger(records=[QueryUsage(timestamp="2000-01-15T10:00:00", cost_usd=5.0)]))
    record_usage(session_id="s", model="m", agent="coder", prompt="p", duration_ms=1,
                 input_tokens=1, output_tokens=1, cost_usd=0.25)

    assert abs(get_month_spent() - 0.25) < 1e-9
    assert abs(get_total_spent() - 5.25) < 1e-9


def test_record_and_calculate_usage(tmp_path, monkeypatch):
    test_usage_file = tmp_path / "usage.json"
    monkeypatch.setattr("claude_agent_cli.usage.USAGE_FILE", test_usage_file)
    monkeypatch.setattr("claude_agent_cli.usage.DEFAULT_CONFIG_DIR", tmp_path)

    assert get_total_spent() == 0.0

    record_usage(
        session_id="session-1",
        model="claude-3-7-sonnet",
        agent="coder",
        prompt="Fix the bug in main.py",
        duration_ms=1200,
        input_tokens=1000,
        output_tokens=250,
        cache_read_tokens=500,
        cost_usd=0.015,
    )

    record_usage(
        session_id="session-1",
        model="claude-3-7-sonnet",
        agent="coder",
        prompt="Run the test suite",
        duration_ms=800,
        input_tokens=800,
        output_tokens=150,
        cache_read_tokens=400,
        cost_usd=0.010,
    )

    assert abs(get_total_spent() - 0.025) < 1e-6

    totals = get_token_totals()
    assert totals["input_tokens"] == 1800
    assert totals["output_tokens"] == 400
    assert totals["cache_read_tokens"] == 900


@pytest.mark.asyncio
async def test_interactive_session_records_per_turn_cost(tmp_path, monkeypatch):
    """ResultMessage.total_cost_usd is a running total; the ledger must store each turn's share."""
    from claude_agent_sdk import ConversationResetMessage, ResultMessage
    from claude_agent_cli.agent import AgentInteractiveSession
    from claude_agent_cli.config import CagentConfig

    monkeypatch.setattr("claude_agent_cli.usage.USAGE_FILE", tmp_path / "usage.json")
    monkeypatch.setattr("claude_agent_cli.usage.DEFAULT_CONFIG_DIR", tmp_path)

    def result(total):
        return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                             num_turns=1, session_id="s", total_cost_usd=total, usage={})

    reset = ConversationResetMessage(new_conversation_id="c2", uuid="u", session_id="s")
    turns = iter([
        [result(1.0)],
        [result(1.5)],
        [result(0.0)],         # error result reporting 0 must not lower the baseline
        [result(1.8)],
        [reset, result(0.2)],  # running total restarts after a reset
    ])

    class FakeClient:
        async def query(self, prompt):
            self.messages = next(turns)

        async def receive_response(self):
            for m in self.messages:
                yield m

    session = AgentInteractiveSession(config=CagentConfig(), api_key="sk-test")
    session.client = FakeClient()

    costs = [(await session.query(str(i))).cost_usd for i in range(5)]

    assert costs == pytest.approx([1.0, 0.5, 0.0, 0.3, 0.2])
    assert get_total_spent() == pytest.approx(2.0)

    # Resuming seeds the running total with what the ledger already holds for that session.
    resumed = AgentInteractiveSession(config=CagentConfig(), api_key="sk-test", resume="s")
    assert resumed.session_spent == pytest.approx(2.0)
