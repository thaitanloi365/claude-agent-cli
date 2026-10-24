# cagent: use your Claude promotional credits in the terminal

**A local coding agent CLI that works like Claude Code and bills your Anthropic API key. It lets you spend Claude Console promotional credits, such as Claude for Startups credits or the monthly API credits on Max and Team plans, on coding in your own repo.**

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Claude Agent SDK](https://img.shields.io/badge/built%20on-Claude%20Agent%20SDK-d97757)
![uv](https://img.shields.io/badge/install-uv-purple)

Maybe you got API credits from the **Claude for Startups program** or a VC partner, or you get the monthly **API credits included with a Max or Team plan**. You want to use them for coding, but Claude Code won't spend them. `cagent` is a terminal coding agent built on Anthropic's official [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python). Like Claude Code, it reads, edits and runs code in your project. The difference is that it draws from those credits.

```bash
uv tool install git+https://github.com/thaitanloi365/claude-agent-cli
cagent auth login          # paste an API key from platform.claude.com
cagent                     # start coding
```

---

## Why this exists

Promotional credits sit in your **Claude Console** organization (platform.claude.com) and only pay for API usage:

- **Claude for Startups:** "Claude Startups credits apply to the first-party Claude API through Claude Console." ([source](https://claude.com/programs/startups))
- **Max and Team plan API credits:** these cover the Claude API, Claude Managed Agents, the **Claude Agent SDK** and the Console playground. They **don't cover Claude Code**. If an organization holds only these credits, Claude Code shows `Credit balance too low · Add funds`. ([source](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers))

Without `cagent`, you can only reach those credits from the Console playground or from code you write yourself. `cagent` gives them a terminal coding agent. It runs the Claude Agent SDK on your machine with your API key, so every turn is billed to that key's Console organization. Included (promotional) credits are spent first, then any credits you bought.

> `cagent` doesn't get around any billing rules. It's a normal Agent SDK client that uses your own API key. Read the terms of your credit program to see what your credits cover.

## cagent vs Claude Code

| | Claude Code | cagent |
|---|---|---|
| Runs locally and reads, edits and runs code in your repo | ✅ | ✅ |
| Covered by Max / Team plan API credits | ❌ | ✅ (via the Agent SDK) |
| Billing | Claude subscription or API key | API key only: Console promotional credits, then Credit balance |
| Login | Your Claude account | API key in its own config dir; your Claude Code login is never touched |
| Your Claude Code settings, project `CLAUDE.md`, skills and MCP servers | ✅ | ✅ reused |
| Spend shown per turn, per session and per month | `/cost` | Bottom bar + `cagent credits` |

---

## ✨ Features

- **Feels like Claude Code:** an interactive REPL with live markdown streaming, diffs for edits, a `/` command menu, `@` file picking, `Shift+Tab` permission modes and `Esc` to interrupt.
- **Uses your Console credits:** runs on `ANTHROPIC_API_KEY`, so promotional credits are spent before your purchased balance.
- **Separate from Claude Code:** keeps its own config dir (`~/.claude-agent-cli/claude_home`), so it never touches your Claude Code login.
- **Reuses your setup:** picks up `~/.claude/settings.json` (model, effort, permissions, hooks), your project's `CLAUDE.md`, your skills and your MCP servers.
- **Tracks spend:** cost and tokens (input, output, cache read/write) for every turn, with session and monthly totals.
- **Agent personas:** built-in `coder`, `architect`, `reviewer`, `tester` and `debugger`, and you can add your own.
- **Works as a one-shot command and in pipes:** `cagent "prompt"`, or `git diff | cagent "review this"`.
- **Built-in credit tool:** an in-process MCP tool (`check_credit_balance`) lets Claude tell you what cagent has spent.

---

## 🚀 Quick Start

### 1. Install

You need Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install git+https://github.com/thaitanloi365/claude-agent-cli
```

Or install from a clone to hack on it:

```bash
git clone https://github.com/thaitanloi365/claude-agent-cli.git
cd claude-agent-cli
uv tool install --editable . --force
```

Either way you get three commands that do the same thing: `cagent`, `claude-agent` and `claude-agent-cli`.

### 2. Add your API key

Create an API key in the Claude Console (https://platform.claude.com), using the organization that holds your promotional credits. Then save it:

```bash
cagent auth login
```

Or export it in your shell (`~/.zshrc`):

```bash
export ANTHROPIC_API_KEY="sk-ant-api03-..."
```

Check which key is active and where it came from:

```bash
cagent auth status
```

### 3. Check your credits

Your promotional credits appear under **Promotional credits** on the [Console billing page](https://platform.claude.com/settings/billing). An API key can't read that balance, so `cagent credits` shows what cagent has spent and links to that page.

---

## 💻 Usage

### Interactive REPL

```bash
cagent
```

- Arrow-key history (`~/.claude-agent-cli/history`); `Ctrl+R` searches it.
- Type `/` for a command menu with descriptions. It also lists Claude Code's own commands and your skills (`/compact`, `/context`, `/init`, `/code-review`, …), which run through the SDK.
- Arguments autocomplete too: `/model `, `/agent `, `/permission `, `/thinking `.
- Type `@` to pick a file from the working directory.
- `Shift+Tab` switches the permission mode (ask → accept edits → plan). The current mode shows in the bottom bar.
- Replies stream as live markdown with highlighted code. A spinner shows while Claude thinks or a tool runs.
- Tool calls show as `● Tool(args)` with a short `⎿` result preview, and edits show a diff.
- Permission requests are an arrow-key (or number) menu.
- `Esc` or `Ctrl+C` stops the current turn without leaving the REPL.
- `Esc+Enter`, `Ctrl+J` or a trailing `\` + `Enter` starts a new line.
- The bottom bar shows the model, agent, permission mode, session spend and cagent's spend this month.

Slash commands:

| Command | What it does |
|---|---|
| `/help` | Command cheat sheet |
| `/credits`, `/balance` | Spend recorded by cagent, plus a link to the real balance |
| `/cost`, `/usage` | Total cost across all queries |
| `/model [sonnet\|opus\|haiku]` | Switch model |
| `/agent [coder\|architect\|reviewer\|tester\|debugger]` | Switch agent persona |
| `/permission [ask\|acceptEdits\|plan\|bypass]` | Change tool permission mode |
| `/thinking [on\|off]` | Show or hide the live thinking stream |
| `/skills`, `/mcp` | List discovered skills and MCP servers |
| `/clear` | Clear the screen |
| `/exit`, `/quit` | Exit |

### One-shot commands

```bash
cagent "Explain the architecture of this directory"
cagent "Fix the lint errors in src/main.py and run the tests"

# Pick an agent persona
cagent --agent reviewer "Audit auth.py for security vulnerabilities"
cagent --agent tester "Generate pytest test cases for utils.py"
cagent --agent architect "Design a migration plan from SQLite to PostgreSQL"

# Pick a model (default: Opus 5.5, or the model set in ~/.claude/settings.json)
cagent -m sonnet "Refactor the core engine"
cagent -m haiku "Summarize this README in 3 bullet points"
```

Other flags: `--mode` (permission mode), `--skills all|off|a,b`, `--mcp all|off|a,b`, `--cwd`, `--resume`, `--no-stream`, `--no-thinking`. Run `cagent --help` for the full list.

### Piping

```bash
git diff | cagent "Review these changes for unintended side effects"
cat error.log | cagent "Diagnose the root cause of this stack trace"
cat schema.sql | cagent "Suggest index optimizations"
```

---

## 📊 Spend tracking

Every turn's cost and token counts are logged to `~/.claude-agent-cli/usage.json`.

```bash
cagent credits    # aliases: cagent balance, cagent usage
```

This shows cagent's spend this month and all time, token totals, and a link to the [Console billing page](https://platform.claude.com/settings/billing). Your real balance is only shown on that page: a normal API key can't read it, and other apps using the same Console organization spend from it too.

---

## 🤖 Agent personas

| Agent | Tools | Role |
|---|---|---|
| `coder` (default) | `Read`, `Write`, `Edit`, `Bash`, `Grep`, `Glob` | General coding: writes and refactors code, creates files, runs commands |
| `architect` | `Read`, `Grep`, `Glob` | Maps dependencies, plans large refactors, weighs trade-offs |
| `reviewer` | `Read`, `Grep`, `Glob` | Reviews code for bugs, edge cases, OWASP issues and anti-patterns |
| `tester` | `Read`, `Write`, `Edit`, `Bash`, `Grep`, `Glob` | Writes unit and integration tests and runs them |
| `debugger` | `Read`, `Edit`, `Bash`, `Grep`, `Glob` | Reads stack traces, tests hypotheses, makes minimal fixes |

```bash
cagent agents list
cagent agents show reviewer
```

### Custom agents

Add your own in `.claude-agent/agents.json` (per project) or `~/.claude-agent-cli/agents.json` (global):

```json
{
  "devops": {
    "description": "DevOps & Infrastructure automation",
    "prompt": "You are a DevOps engineer specialized in Docker, Kubernetes, and CI/CD pipelines.",
    "tools": ["Bash", "Read", "Write", "Edit"],
    "model": "sonnet"
  }
}
```

---

## 🧩 Skills and MCP servers

cagent finds the skills and MCP servers you already use, so you don't need to set them up again:

- **Skills:** `~/.claude/skills`, `~/.agents/skills`, `~/.gemini/config/skills`
- **MCP servers:** `~/.claude/settings.json`, project `.mcp.json` and `.claude/settings.json`, `~/.claude-agent-cli/mcp.json`, `~/.gemini/config/mcp_config.json`

```bash
cagent skills list       # also: cagent skills show <name>, cagent skills sync
cagent mcp list          # also: cagent mcp show <name>
```

---

## ⚙️ Configuration

```bash
cagent config show
```

| Command | Effect |
|---|---|
| `cagent config set-model <model>` | Default model (`opus`, `sonnet`, `haiku` or a full model ID) |
| `cagent config set-effort <level>` | Reasoning effort: `low`, `medium`, `high`, `xhigh`, `max` |
| `cagent config set-agent <agent>` | Default agent persona |
| `cagent config set-mode <mode>` | Default permission mode: `acceptEdits` (default), `ask`, `bypass` |
| `cagent config sync-claude` | Re-read the model and effort from `~/.claude/settings.json` |
| `cagent auth logout` | Remove the saved API key |

Settings live in `~/.claude-agent-cli/config.json`.

---

## 📜 Sessions

```bash
cagent sessions list
cagent sessions resume <session-id>
# or
cagent --resume <session-id>
```

---

## ❓ FAQ

**How do I use Claude for Startups credits for coding?**
The credits apply to the Claude API through the Claude Console. Create an API key in the Console organization that received them, run `cagent auth login`, and use `cagent` as your terminal coding agent.

**Why does Claude Code say "Credit balance too low" when I have promotional credits?**
Some promotional credits don't cover Claude Code. For example, Anthropic's docs say the API credits included with Max and Team plans cover the Agent SDK but not Claude Code. `cagent` runs on the Agent SDK, so it can use them.

**Can I use my Max or Team plan's monthly API credits in the terminal?**
Yes. Claim the credits into a Console organization ([steps](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers)), create an API key there, and point `cagent` at it.

**Will this mess with my Claude Code login or subscription?**
No. cagent uses its own config dir (`~/.claude-agent-cli/claude_home`) and an API key. Claude Code keeps working as before.

**Can I see my remaining credit balance in the CLI?**
Not exactly. An API key can't read the Console balance. `cagent credits` shows what cagent has spent and links to the billing page, where the real balance is shown.

**Does it work with Amazon Bedrock, Google Vertex AI or Azure?**
No. Promotional credits only apply to the first-party Claude API, which is what cagent uses.

---

## 🧪 Development

```bash
make test        # or: uv run pytest -v
```

```
src/claude_agent_cli/
├── cli.py          # Typer CLI and command routing
├── repl.py         # prompt_toolkit REPL and slash commands
├── agent.py        # Claude Agent SDK runner and interactive session
├── streaming.py    # Live token and thinking stream renderer
├── permissions.py  # Interactive tool permission prompts
├── subagents.py    # Built-in and custom agent personas
├── skills.py       # Skill discovery and sync
├── mcp.py          # MCP server discovery
├── tools.py        # In-process SDK MCP tools (spend, git status)
├── usage.py        # Spend tracking and usage ledger
├── sessions.py     # Session history and resume
├── config.py       # Config, model aliases and API key handling
└── ui.py           # Rich terminal styling
```

Issues and PRs are welcome. If cagent helped you put your credits to use, a ⭐ helps other people find it.

---

*cagent is an independent open-source project and is not affiliated with or endorsed by Anthropic. "Claude" and "Claude Code" are trademarks of Anthropic.*
