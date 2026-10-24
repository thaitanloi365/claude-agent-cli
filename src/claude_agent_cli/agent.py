"""Agent execution engine using Claude Agent SDK."""

from __future__ import annotations

import asyncio
import os
import sys
import warnings
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

from claude_agent_sdk import (
    AssistantMessage,
    CanUseToolShadowedWarning,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ConversationResetMessage,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)
from claude_agent_cli.config import (
    CagentConfig,
    get_api_key,
    load_config,
    resolve_model_name,
)
from claude_agent_cli.permissions import (
    create_permission_callback,
    normalize_permission_mode,
)
from claude_agent_cli.streaming import LiveStreamHandler
from claude_agent_cli.subagents import get_agent_definitions, get_agent_spec
from claude_agent_cli.mcp import get_active_mcp_servers
from claude_agent_cli.skills import sync_skills_to_claude_home
from claude_agent_cli.tools import create_inprocess_mcp_server
from claude_agent_cli.ui import (
    console,
    print_error_card,
    print_thinking,
    print_tool_call,
    print_tool_result,
    print_turn_cost,
    screen_lock,
)
from claude_agent_cli.usage import QueryUsage, get_session_spent, record_usage

# The callback is kept on purpose in bypass mode: /permission can leave bypass mid-session, and
# hooks or ask rules still prompt through it, so the SDK's shadowing advisory is noise here.
warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)


class MissingApiKeyError(Exception):
    """Raised when Anthropic API key cannot be resolved."""


def build_agent_options(
    *,
    config: CagentConfig,
    api_key: str,
    model: Optional[str] = None,
    agent_name: Optional[str] = None,
    cwd: Optional[Path] = None,
    permission_mode: Optional[str | Callable[[], str]] = None,
    skills_filter: Optional[list[str]] = None,
    mcp_filter: Optional[list[str]] = None,
    session_id: Optional[str] = None,
    resume: Optional[str] = None,
    fork_session: bool = False,
    include_partial_messages: Optional[bool] = None,
    can_use_tool: Optional[Callable] = None,
    is_interactive: bool = True,
) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions with proper environment isolation and configuration."""
    work_dir = Path(cwd).resolve() if cwd else Path.cwd()
    resolved_model = resolve_model_name(model or config.default_model)
    selected_agent = agent_name or config.default_agent
    raw_mode = permission_mode() if callable(permission_mode) else (permission_mode or config.permission_mode)
    perm_mode = normalize_permission_mode(raw_mode)
    tool_perm_cb = can_use_tool or create_permission_callback(permission_mode or perm_mode, is_interactive=is_interactive)
    should_stream = include_partial_messages if include_partial_messages is not None else config.stream

    # Configure thinking
    thinking_conf: Any = None
    if config.extended_thinking:
        if config.thinking_budget_tokens:
            thinking_conf = {"type": "enabled", "budget_tokens": config.thinking_budget_tokens}
        else:
            thinking_conf = {"type": "adaptive"}
    else:
        thinking_conf = {"type": "disabled"}

    # Base tools configuration
    tools_config = None

    # Retrieve specialized agent definition
    agent_spec = get_agent_spec(selected_agent, project_root=work_dir)
    system_prompt: Any = None
    if agent_spec:
        # Prepend specialized agent persona
        system_prompt = {
            "type": "preset",
            "preset": "claude_code",
            "append": f"\n\n[Active Agent Persona: {selected_agent.upper()}]\n{agent_spec['prompt']}",
        }
        if "tools" in agent_spec:
            tools_config = agent_spec["tools"]

    # Build subagents for multi-agent delegation
    agents_map = get_agent_definitions(default_model=resolved_model, project_root=work_dir)

    # In-process SDK MCP server with system/credit tools
    system_mcp_server = create_inprocess_mcp_server()

    # Synchronize discovered skills into isolated Claude home directory
    sync_skills_to_claude_home(config.claude_home)

    # Aggregate active external MCP servers (Antigravity, Claude settings, workspace .mcp.json)
    external_mcp = get_active_mcp_servers(filter_names=mcp_filter, project_root=work_dir)
    all_mcp_servers: dict[str, Any] = dict(external_mcp)
    all_mcp_servers["cagent-system-tools"] = system_mcp_server

    # Resolve active skills
    active_skills: Any = "all"
    if skills_filter:
        if "all" in skills_filter:
            active_skills = "all"
        elif "none" in skills_filter or "off" in skills_filter:
            active_skills = []
        else:
            active_skills = skills_filter

    # Pass ANTHROPIC_API_KEY and isolate CLAUDE_CONFIG_DIR as Agent SDK
    env_vars = {
        "ANTHROPIC_API_KEY": api_key,
        "CLAUDE_CONFIG_DIR": str(config.claude_home),
        "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
    }

    # Reuse Claude CLI settings.json if configured
    settings_path_str: Optional[str] = None
    if config.reuse_claude_cli_config and config.claude_settings_path and config.claude_settings_path.exists():
        settings_path_str = str(config.claude_settings_path)

    options = ClaudeAgentOptions(
        model=resolved_model,
        fallback_model=resolve_model_name(config.fallback_model) if config.fallback_model else None,
        cwd=work_dir,
        tools=tools_config,
        allowed_tools=[],
        permission_mode=perm_mode,  # type: ignore
        can_use_tool=tool_perm_cb,
        thinking=thinking_conf,
        effort=config.effort,  # type: ignore
        max_budget_usd=config.max_budget_per_run_usd,
        agents=agents_map,
        mcp_servers=all_mcp_servers,
        skills=active_skills,
        setting_sources=["user", "project", "local"],
        settings=settings_path_str,
        session_id=session_id,
        resume=resume,
        fork_session=fork_session,
        env=env_vars,
        include_partial_messages=should_stream,
        # The SDK's 1 MB default is too small for tool results carrying base64 screenshots.
        max_buffer_size=32 * 1024 * 1024,
    )
    if system_prompt:
        options.system_prompt = system_prompt

    return options


async def run_single_turn(
    prompt: str,
    *,
    config: Optional[CagentConfig] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    agent_name: Optional[str] = None,
    cwd: Optional[Path] = None,
    permission_mode: Optional[str] = None,
    skills_filter: Optional[list[str]] = None,
    mcp_filter: Optional[list[str]] = None,
    stream: bool = True,
    show_cost: bool = True,
    show_thinking: Optional[bool] = None,
) -> Optional[ResultMessage]:
    """Execute a single-shot query and handle responses with real-time streaming."""
    active_config = config or load_config()
    resolved_key = get_api_key(api_key)
    if not resolved_key:
        raise MissingApiKeyError(
            "Anthropic API Key not found! Run 'cagent auth login' or export ANTHROPIC_API_KEY."
        )

    is_interactive = sys.stdin.isatty()
    options = build_agent_options(
        config=active_config,
        api_key=resolved_key,
        model=model,
        agent_name=agent_name,
        cwd=cwd,
        permission_mode=permission_mode,
        skills_filter=skills_filter,
        mcp_filter=mcp_filter,
        include_partial_messages=stream,
        is_interactive=is_interactive,
    )

    result_message: Optional[ResultMessage] = None
    turn_agent = agent_name or active_config.default_agent
    turn_model = model or active_config.default_model

    thinking_visible = show_thinking if show_thinking is not None else active_config.show_thinking
    handler = LiveStreamHandler(show_thinking=thinking_visible, is_interactive=is_interactive)

    try:
        async for msg in query(prompt=prompt, options=options):
            async with screen_lock:
                handler.handle_message(msg)
            if isinstance(msg, ResultMessage):
                result_message = msg
                usage = record_usage(
                    session_id=msg.session_id,
                    model=turn_model,
                    agent=turn_agent,
                    prompt=prompt,
                    duration_ms=msg.duration_ms,
                    input_tokens=msg.usage.get("input_tokens", 0) if isinstance(msg.usage, dict) else 0,
                    output_tokens=msg.usage.get("output_tokens", 0) if isinstance(msg.usage, dict) else 0,
                    cache_read_tokens=msg.usage.get("cache_read_input_tokens", 0) if isinstance(msg.usage, dict) else 0,
                    cache_creation_tokens=msg.usage.get("cache_creation_input_tokens", 0) if isinstance(msg.usage, dict) else 0,
                    cost_usd=msg.total_cost_usd or 0.0,
                )
                if show_cost:
                    print_turn_cost(usage)

    finally:
        handler.close()

    return result_message


class AgentInteractiveSession:
    """Manages an interactive, multi-turn conversation session."""

    def __init__(
        self,
        config: Optional[CagentConfig] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        agent_name: Optional[str] = None,
        cwd: Optional[Path] = None,
        permission_mode: Optional[str] = None,
        skills_filter: Optional[list[str]] = None,
        mcp_filter: Optional[list[str]] = None,
        resume: Optional[str] = None,
        show_thinking: Optional[bool] = None,
    ):
        self.config = config or load_config()
        resolved_key = get_api_key(api_key)
        if not resolved_key:
            raise MissingApiKeyError(
                "Anthropic API Key not found! Run 'cagent auth login' or export ANTHROPIC_API_KEY."
            )
        self.api_key = resolved_key
        self.model = model or self.config.default_model
        self.agent_name = agent_name or self.config.default_agent
        self.cwd = cwd or Path.cwd()
        self.permission_mode = normalize_permission_mode(permission_mode or self.config.permission_mode)
        self.skills_filter = skills_filter
        self.mcp_filter = mcp_filter
        self.resume = resume
        self.show_thinking = show_thinking if show_thinking is not None else self.config.show_thinking
        self.client: Optional[ClaudeSDKClient] = None
        self.active_session_id: Optional[str] = None
        self.last_result: Optional[ResultMessage] = None
        self.rate_limit: Optional[RateLimitInfo] = None  # sent only when the auth has a usage window
        # ResultMessage.total_cost_usd is a running total for the connection, not per turn,
        # and a resumed session's total starts from the spend saved in its transcript.
        self.session_spent = get_session_spent(resume) if resume else 0.0
        self.persona_note = ""

    async def start(self) -> None:
        """Start the ClaudeSDKClient connection."""
        options = build_agent_options(
            config=self.config,
            api_key=self.api_key,
            model=self.model,
            agent_name=self.agent_name,
            cwd=self.cwd,
            permission_mode=lambda: self.permission_mode,
            skills_filter=self.skills_filter,
            mcp_filter=self.mcp_filter,
            resume=self.resume,
            include_partial_messages=True,
            is_interactive=True,
        )
        self.client = ClaudeSDKClient(options=options)
        await self.client.connect()

    async def restart(self) -> None:
        """Reconnect with current settings, keeping the conversation."""
        if self.active_session_id:
            self.resume = self.active_session_id
        await self.close()
        await self.start()

    async def switch_agent(self, name: str) -> None:
        """Change persona mid-conversation.

        A resumed session keeps the system prompt saved in its transcript (prompt_snapshot),
        so the new persona's instructions ride on the next message; the restart applies its tools.
        """
        previous = self.agent_name
        self.agent_name = name
        spec = get_agent_spec(name, project_root=self.cwd)
        try:
            await self.restart()
        except Exception:
            self.agent_name = previous
            raise
        if spec and self.active_session_id:
            self.persona_note = (
                f"[Agent persona switched to {name.upper()}. These instructions replace any earlier persona.]\n"
                f"{spec['prompt']}\n\n"
            )

    async def set_permission_mode(self, mode: str) -> None:
        """Dynamically update permission mode at runtime."""
        norm_mode = normalize_permission_mode(mode)
        self.permission_mode = norm_mode
        if self.client:
            await self.client.set_permission_mode(norm_mode)  # type: ignore

    async def query(self, prompt: str) -> QueryUsage:
        """Send a prompt and process streamed assistant messages until ResultMessage."""
        if not self.client:
            raise RuntimeError("Agent session is not started. Call start() first.")

        await self.client.query(self.persona_note + prompt)
        self.persona_note = ""
        last_result: Optional[ResultMessage] = None
        handler = LiveStreamHandler(show_thinking=self.show_thinking, is_interactive=True)

        try:
            async for msg in self.client.receive_response():
                async with screen_lock:
                    handler.handle_message(msg)
                if isinstance(msg, ConversationResetMessage):
                    self.session_spent = 0.0
                if isinstance(msg, RateLimitEvent):
                    self.rate_limit = msg.rate_limit_info
                if isinstance(msg, ResultMessage):
                    last_result = msg
                    self.active_session_id = msg.session_id
        finally:
            handler.close()

        if not last_result:
            return QueryUsage()

        self.last_result = last_result
        running_total = last_result.total_cost_usd or 0.0
        # Error results can report 0, so never let the baseline fall; resets are handled above.
        turn_cost = max(0.0, running_total - self.session_spent)
        self.session_spent = max(self.session_spent, running_total)
        usage = record_usage(
            session_id=last_result.session_id,
            model=self.model,
            agent=self.agent_name,
            prompt=prompt,
            duration_ms=last_result.duration_ms,
            input_tokens=last_result.usage.get("input_tokens", 0) if isinstance(last_result.usage, dict) else 0,
            output_tokens=last_result.usage.get("output_tokens", 0) if isinstance(last_result.usage, dict) else 0,
            cache_read_tokens=last_result.usage.get("cache_read_input_tokens", 0) if isinstance(last_result.usage, dict) else 0,
            cache_creation_tokens=last_result.usage.get("cache_creation_input_tokens", 0) if isinstance(last_result.usage, dict) else 0,
            cost_usd=turn_cost,
        )
        print_turn_cost(usage, self.session_spent)
        return usage

    async def interrupt(self) -> None:
        """Interrupt current query execution."""
        if self.client:
            try:
                await self.client.interrupt()
            except Exception:
                pass

    async def close(self) -> None:
        """Close the agent session."""
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None
