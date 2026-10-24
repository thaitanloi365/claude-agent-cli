"""Real-time token and thinking stream renderer for claude-agent-cli."""

from __future__ import annotations

import sys
from typing import Any, Optional

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from rich.markdown import Markdown
from rich.segment import Segment

from claude_agent_cli.ui import (
    console,
    print_markdown,
    print_thinking,
    print_tool_call,
    print_tool_result,
    start_live,
    start_spinner,
    stop_live,
    stop_spinner,
)


def _result_text(content: Any) -> str:
    """Flatten tool result content (a string or a list of content blocks) to text."""
    if isinstance(content, list):
        return "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return str(content or "")


class _MarkdownTail:
    """The last screenful of a growing markdown reply, so long replies stay in view while streaming."""

    def __init__(self, handler: "LiveStreamHandler"):
        self.handler = handler

    def __rich_console__(self, console, options):
        lines = console.render_lines(Markdown(self.handler.text), options.update(height=None), pad=False)
        for line in lines[-max(1, options.max_height - 1):]:
            yield from line
            yield Segment.line()


class LiveStreamHandler:
    """Handles real-time streaming deltas (thinking, text) and batch messages.

    In a terminal, assistant text renders as live markdown and is printed once
    complete; when piped, text is written verbatim as it arrives. A spinner
    covers the gaps while Claude thinks or a tool runs.
    """

    def __init__(self, *, show_thinking: bool = True, is_interactive: bool = True):
        self.show_thinking = show_thinking
        self.is_interactive = is_interactive
        self.in_thinking = False
        self.in_text = False
        self.streamed_text = False
        self.streamed_thinking = False
        self.text = ""
        self.live = False
        self.tool_names: dict[str, str] = {}
        self.last_call_id: Optional[str] = None
        start_spinner()

    def _start_thinking(self) -> None:
        if self.in_thinking:
            return
        stop_spinner()
        self.in_thinking = True
        self.streamed_thinking = True
        sys.stdout.write(f"{self._ansi('2;3')}💭 Thinking:\n")
        sys.stdout.flush()

    def _end_thinking(self) -> None:
        if self.in_thinking:
            sys.stdout.write(f"{self._ansi('0')}\n\n")
            sys.stdout.flush()
            self.in_thinking = False

    @staticmethod
    def _ansi(code: str) -> str:
        """An SGR escape for terminals; nothing when piped."""
        return f"\033[{code}m" if console.is_terminal else ""

    def _start_text(self) -> None:
        self._end_thinking()
        if self.in_text:
            return
        self.in_text = True
        self.streamed_text = True
        self.text = ""
        if console.is_terminal:
            start_live(_MarkdownTail(self))
            self.live = True

    def _append_text(self, chunk: str) -> None:
        self.text += chunk
        if not self.live:
            sys.stdout.write(chunk)
            sys.stdout.flush()

    def _end_text(self) -> None:
        if not self.in_text:
            return
        self.in_text = False
        if self.live:
            stop_live()
            self.live = False
            print_markdown(self.text)
        else:
            sys.stdout.write("\n")
            sys.stdout.flush()

    def _end_blocks(self) -> None:
        self._end_thinking()
        self._end_text()

    def handle_stream_event(self, event_data: dict[str, Any]) -> None:
        """Process a raw Anthropic SSE event inside StreamEvent."""
        event_type = event_data.get("type")

        if event_type == "content_block_start":
            cb = event_data.get("content_block", {})
            cb_type = cb.get("type")
            if cb_type == "thinking":
                # The header waits for real text: some models stream no thinking content.
                self._end_text()
                start_spinner()
            elif cb_type == "text":
                self._start_text()
            else:
                self._end_blocks()
                if cb_type == "tool_use":
                    start_spinner(f"{cb.get('name', 'Tool')}…")

        elif event_type == "content_block_delta":
            delta = event_data.get("delta", {})
            delta_type = delta.get("type")

            if delta_type == "thinking_delta" and self.show_thinking:
                chunk = delta.get("thinking", "")
                if not self.in_thinking:
                    chunk = chunk.lstrip("\n")
                if chunk:
                    self._start_thinking()
                    sys.stdout.write(chunk)
                    sys.stdout.flush()

            elif delta_type == "text_delta":
                chunk = delta.get("text", "")
                if chunk:
                    self._start_text()
                    self._append_text(chunk)

        elif event_type in ("content_block_stop", "message_stop", "message_delta"):
            self._end_blocks()

    def handle_message(self, msg: Any) -> None:
        """Handle any message type yielded by the SDK client/query."""
        if isinstance(msg, StreamEvent):
            if isinstance(msg.event, dict):
                self.handle_stream_event(msg.event)

        elif isinstance(msg, AssistantMessage):
            # Ensure ANSI styles are closed if stream stopped abruptly
            self._end_blocks()

            for block in msg.content:
                if isinstance(block, TextBlock):
                    # Only print if we didn't already stream it
                    if not self.streamed_text:
                        stop_spinner()
                        print_markdown(block.text)
                elif isinstance(block, ThinkingBlock):
                    # Only print if we didn't already stream it
                    if not self.streamed_thinking and self.show_thinking:
                        print_thinking(block.thinking)
                elif isinstance(block, ToolUseBlock):
                    print_tool_call(block.name, block.input)
                    self.tool_names[block.id] = block.name
                    self.last_call_id = block.id
                    start_spinner(f"Running {block.name}…")

            # Reset flags for next turn
            self.streamed_text = False
            self.streamed_thinking = False

        elif isinstance(msg, UserMessage):
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        # Name the tool only when its call is not the line right above.
                        print_tool_result(
                            self.tool_names.get(block.tool_use_id, "Tool"),
                            _result_text(block.content),
                            is_error=block.is_error or False,
                            show_name=block.tool_use_id != self.last_call_id,
                        )
                        self.last_call_id = None
                        start_spinner()

    def close(self) -> None:
        """Finish any open block and remove the spinner."""
        self._end_blocks()
        stop_spinner()
