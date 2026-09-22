# Hosts — plat-harness is a CLI, not a window

## What it is

| Surface | plat-harness | BOXSCORE (today) |
|---|---|---|
| Operator UX | CLI: `ask` / `scoreboard` / `underwrite` | Optional ratatui TUI + HTTP `:3818` |
| Product intent | Agent-agnostic control plane; dashboard later reads the **same** certified board | Local-first ops calc; TUI is not the V2 cockpit |
| Headless | Yes. Exit codes + JSON stderr/stdout | Yes (`boxscore` CLI/API) |
| Vendor plugin | Not required. Do not add `claude-agent-sdk` | N/A |

NORTHSTAR: the API is the CLI forever so humans and agents share a contract.
A later Next.js cockpit may **display** scoreboard JSON. It must not become a
second arithmetic engine. V2 discarded “ratatui as the product surface.”

## How each host should call it

**Cursor / Claude Code / Codex / Aider / Hermes (any ReAct agent with a shell)**

1. Paste `SPARK_OPERATOR.md` as the session system prompt (or `@prompts/SPARK_OPERATOR.md`).
2. Give the agent a shell. The only calc tool is `plat-harness`.
3. Do not wrap this in `.claude/commands` as the runtime. Slash markdown may
   remain as docs.
4. Optional later: a thin MCP server that **shells the same CLI** (same JSON).
   That is a convenience adapter, not a second product.

**DGX Spark (FT Qwen or a frontier model served by vLLM/Ollama)**

Two shapes, in order of honesty:

1. **Agent-with-shell (works today).** The Spark session (Hermes, Open WebUI
   tools, Cursor remote, ssh) runs or remotes `plat-harness` as in
   `SPARK_OPERATOR.md`. Qwen chooses commands; CLI returns JSON.
2. **In-process ModelRouter (not wired).** VPS harness loop calls Spark
   `POST /v1/chat/completions` with tools. Still NullModel in pytest.
   Prefer OpenAI-compatible tools, not Anthropic Messages, for this path.
   BOXSCORE `ask` currently posts Anthropic-shaped `/v1/messages` — do not
   make that the harness long-term contract.

**Claude Code / Codex specifically**

They already have Bash. Install plat-harness on the data host, export
`PLAT_HARNESS_*`, paste the metaprompt. No plugin marketplace step.

## ChatGPT / Claude app users (non-technical)

Typical public users will be GPs/analysts who already talk to Claude or
ChatGPT in an app, not people who live in a terminal. For them:

| Surface | Role |
|---|---|
| **Cockpit (later)** | The real non-technical product: a local/VPS web UI that opens scoreboard, underwrite, recon, comps. Same CLI JSON underneath. |
| **MCP (later)** | Chat-app adapter that calls this CLI. **Claude Desktop / Cursor:** local stdio MCP is the honest fit (tools run on the machine that holds OM/T12). **ChatGPT / claude.ai in a browser:** remote HTTPS MCP only — that is a hosted connector, not a laptop folder drop. Do not tunnel live deal rooms to OpenAI/Anthropic cloud without an explicit product decision. |
| **Pasted prompt only** (Custom GPT / Claude Project) | Useful as instructions. Insufficient: the hosted chat cannot see a local OM/T12 unless MCP or a cockpit is running. Uploading the OM into the chat is the failure mode we are avoiding. |

So: **cockpit + local MCP** is the best UX for that audience. A plugin that only
injects `SPARK_OPERATOR.md` will still hallucinate returns. ChatGPT-as-the-app
is the weaker host until you are willing to host a remote MCP with OAuth —
which fights the local-first confidentiality rule.

**What we are building now:** CLI + certified refusals (Slice 0/A/E). MCP and
the cockpit are **directionally planned**, not in the current slice.
Do not vendor `claude-agent-sdk`. Do not make ChatGPT the runtime. Policy
YAML (`policies/`) is a GP-hurdle plugin, not a ChatGPT plugin.

## What not to build

- A second TUI “like Boxscore” as the V2 operator home.
- `claude -p` sibling dispatch as the way to underwrite.
- Overnight unattended IC publish.
- Sending live rent rolls to Spark as prompt context. CLI on the VPS; model
  sees tool JSON only, redacted.
