"""Tests for real-time LiveStreamHandler."""

from io import StringIO
import sys
from unittest.mock import patch
import pytest

from claude_agent_sdk import (
    AssistantMessage,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_cli.streaming import LiveStreamHandler


def test_streaming_thinking_and_text():
    """Verify thinking deltas and text deltas stream to stdout in real time."""
    handler = LiveStreamHandler(show_thinking=True)

    with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
        # Thinking start & delta
        handler.handle_stream_event({"type": "content_block_start", "content_block": {"type": "thinking"}})
        handler.handle_stream_event({
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "Let me plan step 1..."},
        })
        handler.handle_stream_event({"type": "content_block_stop"})

        # Text start & delta
        handler.handle_stream_event({"type": "content_block_start", "content_block": {"type": "text"}})
        handler.handle_stream_event({
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Here is the solution."},
        })
        handler.handle_stream_event({"type": "content_block_stop"})

        output = mock_stdout.getvalue()
        assert "Thinking" in output
        assert "Let me plan step 1..." in output
        assert "Here is the solution." in output
        assert "\033[" not in output  # no colour codes when piped


def test_thinking_suppression():
    """When show_thinking is False, thinking deltas are omitted."""
    handler = LiveStreamHandler(show_thinking=False)

    with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
        handler.handle_stream_event({
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "Secret thoughts"},
        })
        handler.handle_stream_event({
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Visible answer."},
        })

        output = mock_stdout.getvalue()
        assert "Secret thoughts" not in output
        assert "Visible answer." in output


def test_no_duplicate_printing_after_stream():
    """When text was already streamed live, AssistantMessage TextBlock is not reprinted."""
    handler = LiveStreamHandler(show_thinking=True)

    with patch("sys.stdout", new_callable=StringIO) as mock_stdout, \
         patch("claude_agent_cli.streaming.console.print") as mock_console:

        # Stream text delta
        event = StreamEvent(
            uuid="1",
            session_id="s1",
            event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Streamed text"}},
        )
        handler.handle_message(event)

        # Batch AssistantMessage arrives
        msg = AssistantMessage(content=[TextBlock(text="Streamed text")], model="claude-opus-5-5")
        handler.handle_message(msg)

        # console.print should NOT be called for TextBlock because it was already streamed
        mock_console.assert_not_called()


def test_fallback_printing_when_not_streamed():
    """When stream was disabled or no deltas arrived, AssistantMessage prints content."""
    handler = LiveStreamHandler(show_thinking=True)

    with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
        msg = AssistantMessage(content=[TextBlock(text="Batch [x] text " + "word " * 30)], model="claude-opus-5-5")
        handler.handle_message(msg)

        # Piped output is verbatim: no markup parsing, no wrapping at 80 columns
        assert mock_stdout.getvalue() == "Batch [x] text " + "word " * 30 + "\n"


def test_tool_use_and_result_handling():
    """ToolUseBlock and ToolResultBlock trigger UI display functions."""
    handler = LiveStreamHandler()

    with patch("claude_agent_cli.streaming.print_tool_call") as mock_call, \
         patch("claude_agent_cli.streaming.print_tool_result") as mock_result:

        # Tool use in AssistantMessage
        assistant_msg = AssistantMessage(
            content=[ToolUseBlock(id="t1", name="Bash", input={"command": "ls"})],
            model="claude-opus-5-5",
        )
        handler.handle_message(assistant_msg)
        mock_call.assert_called_once_with("Bash", {"command": "ls"})

        # Tool result right under its call: named, but the label is hidden
        user_msg = UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="file.txt", is_error=False)])
        handler.handle_message(user_msg)
        mock_result.assert_called_once_with("Bash", "file.txt", is_error=False, show_name=False)


def test_parallel_tool_results_show_names_and_flatten_content():
    """Results that don't follow their own call are labelled; list content becomes text."""
    handler = LiveStreamHandler()

    with patch("claude_agent_cli.streaming.print_tool_call"), \
         patch("claude_agent_cli.streaming.print_tool_result") as mock_result:
        handler.handle_message(AssistantMessage(
            content=[
                ToolUseBlock(id="a", name="Read", input={"file_path": "a.py"}),
                ToolUseBlock(id="b", name="Grep", input={"pattern": "x"}),
            ],
            model="claude-opus-5-5",
        ))
        handler.handle_message(UserMessage(content=[
            ToolResultBlock(tool_use_id="a", content=[{"type": "text", "text": "line1"}], is_error=False),
            ToolResultBlock(tool_use_id="b", content="hit", is_error=False),
        ]))

        assert mock_result.call_args_list[0].args == ("Read", "line1")
        assert mock_result.call_args_list[0].kwargs["show_name"] is True
        assert mock_result.call_args_list[1].kwargs["show_name"] is True
