# Prompts — how to drive plat-harness from any agent

plat-harness is **not** a Boxscore-style TUI and **not** a Cursor/Claude plugin
that must be installed. It is a **CLI control plane**. Any coding agent
(Cursor, Codex, Claude Code, Hermes, Aider, a Spark session with FT Qwen or a
frontier model) drives it the same way: **subprocess `plat-harness`**, then
treat stdout/stderr JSON as the only numeric source.

Paste [`SPARK_OPERATOR.md`](SPARK_OPERATOR.md) as the system / developer prompt
on the DGX Spark (or anywhere). Fill the `FACTS` block. Do not invent CoC/IRR.

Host-specific invocation notes: [`HOSTS.md`](HOSTS.md).
Folder-drop campaign (generic): [`DEAL_ROOM_CAMPAIGN.md`](DEAL_ROOM_CAMPAIGN.md).
GPU-box bootstrap (clone + rsync from a data host): [`SPARK_BOOTSTRAP.md`](SPARK_BOOTSTRAP.md).
Product sequence: [`../docs/ROADMAP.md`](../docs/ROADMAP.md).

Live GP overlay paths (ops Standardized, deal rooms, millage sources) stay
**out of this public tree**. Point env at them on the machine that holds the
files. Prefer running the CLI **where the data already lives** (typically the
VPS). The Spark box hosts the model; it should SSH/Tailscale to the CLI rather
than copying OM / rent rolls / `*.db` onto the GPU node.
