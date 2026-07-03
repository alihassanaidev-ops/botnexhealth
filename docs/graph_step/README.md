# Graphify Knowledge-Graph Setup Guide (reusable)

> **Purpose.** Hand this file to Claude Code (or follow it yourself) to set up the **Graphify**
> code knowledge graph + MCP integration in *any* repository, from scratch, with no further research.
> It captures everything learned configuring Graphify for the `botnexhealth` repo, generalized so it
> works anywhere. Where a value is environment-specific, it is called out with `‹angle brackets›`.

- **Project:** Graphify — turns a folder of code (and optionally docs/PDFs/images) into a queryable knowledge graph, served to AI assistants over MCP.
- **PyPI package:** `graphifyy` (note the **double-y**). **CLI command:** `graphify`. **MCP server exe:** `graphify-mcp`.
- **Repo / docs:** https://github.com/safishamsi/graphify (default branch `v8`).
- **Verified with:** `graphifyy v0.8.49`, Python 3.11, Windows 11 / PowerShell, Claude Code.

---

## 0. TL;DR runbook (copy-paste)

For a **code-only, offline, zero-cost** graph (the recommended default). Run from the repo root.

```bash
# 1. Install (the [mcp] extra is required to run the MCP server)
uv tool install "graphifyy[mcp]"          # → installs exes `graphify` and `graphify-mcp` into ~/.local/bin

# 2. Make the build code-only so it never calls a paid LLM (see §3 for the file body)
#    Create .graphifyignore at the repo root (excludes docs/markdown/images).

# 3. Build the graph (AST/tree-sitter only; no API key needed for code)
graphify .                                 # → writes graphify-out/graph.json

# 4. Generate the human report offline (skip LLM community naming)
graphify cluster-only . --no-label

# 5. Register Graphify's Claude Code skill + helper hooks (recommended by upstream)
graphify install --project --platform claude

# 6. Wire the MCP server into Claude Code  → create/merge .mcp.json (see §5)

# 7. Keep the generated graph out of git
echo "graphify-out/" >> .gitignore

# 8. RESTART Claude Code so it loads the new MCP server (MCP binds at startup)
```

Then verify with §8. Everything below is the detailed version with rationale and troubleshooting.

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.10+** | Graphify needs it. A real interpreter (not just the Windows Store stub) must back `uv`. |
| **`uv`** (recommended) | `uv tool install` puts Graphify in an isolated env. Alternatives: `pipx install graphifyy`, `pip install graphifyy`. |
| **Claude Code** | The target assistant. MCP servers are loaded **at launch** (see §7). |
| **git** | For `git check-ignore` and to keep the generated graph out of version control. |

**Windows gotcha (important):** the bare `python` on PATH is often the Microsoft Store stub
(`...\WindowsApps\python.exe`). That does **not** matter for installing Graphify (`uv tool install`
uses its own managed env), but it **does** matter for the MCP server command — so reference the
installed `graphify-mcp.exe` by **full path** in `.mcp.json` rather than relying on `python -m ...`
(see §5).

---

## 2. Install Graphify

```bash
uv tool install "graphifyy[mcp]"
```

- Installs **two executables**: `graphify` (CLI) and `graphify-mcp` (MCP server).
- On Windows they land in `‹%USERPROFILE%›\.local\bin\` (e.g. `C:\Users\You\.local\bin\`).
  **This directory is frequently NOT on the system PATH** — `uv` prints a warning to that effect.
  - Either add it to PATH (`uv tool update-shell`, then restart the shell), **or**
  - reference the full exe path where needed (the approach this guide uses for `.mcp.json`).
- For shell sessions where you call the CLI, prepend it for the session:
  - PowerShell: `$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"`
  - bash: `export PATH="$HOME/.local/bin:$PATH"`

> Optional extras (only if you later ingest non-code content): `graphifyy[pdf]`, `[office]`, `[video]`,
> `[neo4j]`, `[all]`, etc. Not needed for a code-only graph.

Confirm the install:

```bash
uv tool list          # should show graphifyy + the two exes
graphify --help
```

---

## 3. Make the build code-only (zero cost) — create `.graphifyignore`

**Why:** `graphify .` extracts **code with tree-sitter locally (free, offline, no key)**, but if the
corpus contains **docs / markdown / images**, Graphify wants an **LLM API key** to semantically extract
them and will **abort** with:

```
error: no LLM API key found (N doc/paper/image file(s) need semantic extraction).
A code-only corpus needs no key.
```

To guarantee no API calls and no cost, exclude those file types so the corpus is code-only. Create
`.graphifyignore` at the **repo root** (same syntax as `.gitignore`; it is merged with `.gitignore`):

```gitignore
# Graphify ignore — keeps the graph build code-only (AST/tree-sitter, fully offline, no LLM API cost).
# Remove these doc/image excludes and set an LLM API key (e.g. ANTHROPIC_API_KEY) to also
# ingest documentation and images for richer semantic edges.

# Documentation / prose (would trigger paid LLM semantic extraction)
*.md
*.mdx
*.rst
*.txt
*.pdf
*.docx
*.xlsx
*.html
*.htm

# Images (would trigger paid LLM vision extraction)
*.png
*.jpg
*.jpeg
*.gif
*.svg
*.webp
*.ico

# Generated graph output (avoid self-ingestion)
graphify-out/
```

> **Iterate if needed.** Run `graphify .` and read the scan line, e.g.
> `[graphify extract] found 447 code, 1 docs, 0 papers, 0 images`. If it still reports `>0 docs/images`,
> a file type slipped through — note: `.html` counts as a "doc", images as "images". Add the offending
> extension and re-run until it reports **`0 docs, 0 papers, 0 images`**. (For this repo the only
> non-obvious one was `index.html`.)

---

## 4. Build the graph

```bash
graphify .
```

- Default build = **AST extraction (local, free)** + clustering/community detection.
- Output is written to **`graphify-out/`**:
  - `graph.json` — the full queryable graph (this is what the MCP server serves; can be several MB).
  - `GRAPH_REPORT.md` — architecture summary (generated by the cluster step, see below).
  - `.graphify_analysis.json`, `.graphify_labels.json`, `manifest.json`, `cache/`.
- Example result (this repo): `447 code files → 5,225 nodes, 15,909 edges, 221 communities`.

Generate the human-readable report **offline** (skip the LLM that would otherwise name communities):

```bash
graphify cluster-only . --no-label
```

- `--no-label` keeps placeholder community names (`Community 1`, …) so **no LLM is called**.
- `graph.html` (interactive viz) is **skipped automatically when the graph exceeds ~5000 nodes**
  ("too large for HTML viz"). `GRAPH_REPORT.md` is still produced. To force the viz, raise the limit:
  `GRAPHIFY_VIZ_NODE_LIMIT` env var, or pass a smaller corpus.

---

## 5. Configure the MCP server for Claude Code — `.mcp.json`

`graphify install` does **not** write `.mcp.json`; you create it. Put this at the repo root (merge into
an existing `mcpServers` block if one exists):

```json
{
  "mcpServers": {
    "graphify": {
      "command": "‹C:\\Users\\You\\.local\\bin\\graphify-mcp.exe›",
      "args": ["graphify-out/graph.json"],
      "type": "stdio"
    }
  }
}
```

- **`command`** — use the **full path** to `graphify-mcp.exe` (because `~/.local/bin` is usually not on
  PATH). Find it with `(Get-Command graphify-mcp).Source` (PowerShell) or `which graphify-mcp` (bash).
  - Cross-platform / PATH-on-system alternative: `"command": "graphify-mcp"` (only if the dir is on PATH).
- **`args`** — path to the graph, **relative to the repo root** (Claude Code launches MCP servers with
  cwd = project root). `graphify-out/graph.json` is the default the server also assumes.
- The server is **stdio** by default. (HTTP transport exists for a shared team server:
  `graphify-mcp graphify-out/graph.json --transport http --host 0.0.0.0 --port 8080 --api-key "$SECRET"`,
  registered with a `"url"` + `AUTHORIZATION` header instead of `command`/`args`.)

**MCP tools exposed by the server** (verified via JSON-RPC `tools/list`):
`query_graph`, `get_node`, `get_neighbors`, `get_community`, `god_nodes`, `graph_stats`,
`shortest_path`, `list_prs`, `get_pr_impact`, `triage_prs`.

---

## 6. Register the Graphify skill + helper hooks (recommended by upstream)

```bash
graphify install --project --platform claude
```

This is the project's recommended "register with your assistant" step. **Project-scoped** (`--project`)
writes into the repo (vs. user-global). It performs several actions — know what they touch:

| It creates / modifies | What |
|---|---|
| `.claude/skills/graphify/SKILL.md` + `references/` | A Claude Code **skill** describing how to use Graphify (the `/graphify` skill). |
| `.claude/settings.json` | Adds **PreToolUse hooks** that nudge "run `graphify query` before grep/read" when `graphify-out/graph.json` exists. |
| `CLAUDE.md` (repo root) | Appends a short **graphify section** with the query-first rules. |

Notes:
- The PreToolUse hooks shell out to `python3` with `… 2>/dev/null || true` fallbacks, so they
  **degrade gracefully** if `python3` isn't on PATH (common on Windows) — they simply won't fire.
- Undo with `graphify uninstall` (add `--purge` to also delete `graphify-out/`).

---

## 7. Keep the generated graph out of git, then RESTART

```bash
echo "graphify-out/" >> .gitignore
git check-ignore graphify-out/graph.json   # confirms it's ignored
```

- `graphify-out/graph.json` is large and regenerable — **gitignore it.** (Graphify's docs mention
  committing it for team sharing; for most repos, ignoring it and rebuilding is cleaner. Your call.)

**⚠️ Restart Claude Code now.** MCP servers are bound **once at startup**. Until you restart:
- the new `graphify` server is **not** active, and
- if you are replacing another graph MCP server, the **old one is still loaded** for the session.

After restart, the `graphify` MCP tools become available and the graph is live.

---

## 8. Verify the setup

```bash
# CLI sanity check — should return real symbols from THIS repo:
graphify query "what are the main entry points and how do routes connect to services?" --budget 400
graphify explain "‹SomeClassOrFunction›"

# Confirm the MCP server starts and loads the graph (it blocks as a stdio server — Ctrl-C to stop):
graphify-mcp graphify-out/graph.json
```

After restarting Claude Code, confirm the `graphify` server shows as connected and call a tool
(e.g. `graph_stats` or `query_graph`). A successful `tools/list` returns the 10 tools listed in §5.

---

## 9. Files created / modified — checklist

| Path | Action | Notes |
|---|---|---|
| `.graphifyignore` | **create** | §3 — keeps build code-only/offline. |
| `graphify-out/` | **generated** | by `graphify .` — gitignored. |
| `.mcp.json` | **create/merge** | §5 — registers the `graphify` MCP server. |
| `.gitignore` | **modify** | add `graphify-out/`. |
| `.claude/skills/graphify/` | **generated** | by `graphify install` (skill + references). |
| `.claude/settings.json` | **modified** | by `graphify install` (adds PreToolUse hooks). |
| `CLAUDE.md` (root) | **created/appended** | by `graphify install` (graphify section). |

---

## 10. Replacing an existing graph tool (e.g. `code-review-graph`)

If the repo previously used another graph MCP, remove its footprint **before/alongside** the steps above:

1. **Delete its data dir** (e.g. `.code-review-graph/` and its `graph.db`).
2. **Remove its server** from `.mcp.json` (replace with the `graphify` entry from §5).
3. **Strip its hook commands** from `.claude/settings.json` (e.g. `SessionStart`/`PostToolUse` calls that
   ran `uvx <oldtool> …`) and any `*.sh`/`*.ps1` hook scripts that invoke it.
4. **Update docs/skill files** that reference the old tool's MCP tools to the Graphify tools in §5.
5. **Heads-up:** a still-running old MCP server (loaded at session start) may **recreate its data dir**
   until you **restart Claude Code**. Deleting the folder mid-session is not enough on its own.
6. Old machine-local permission entries in `.claude/settings.local.json` (gitignored) are inert; leave
   or prune them.

---

## 11. Common issues & prerequisites

| Symptom | Cause / Fix |
|---|---|
| `error: no LLM API key found (N doc/paper/image files…)` | The corpus isn't code-only. Add the offending types to `.graphifyignore` (§3) and re-run until `0 docs, 0 papers, 0 images`. |
| `graphify` / `graphify-mcp` "not found" | `~/.local/bin` not on PATH. Prepend it for the session (§2) or use the full exe path (§5). |
| MCP server doesn't appear in Claude Code | You didn't **restart** (§7), or `.mcp.json` `command` path is wrong, or `args` graph path doesn't resolve from the project root. |
| Graph looks stale vs. the working tree | Refresh: `graphify update .` (fast, code-only, no LLM), then **restart** so the server reloads. |
| Community names are `Community N` | Expected with `--no-label`/offline. Set an LLM key and run `graphify label .` for named clusters. |
| `graph.html` not generated | Graph > ~5000 nodes; viz is skipped. Raise `GRAPHIFY_VIZ_NODE_LIMIT` if you need it. |
| Windows `python` is the Store stub | Doesn't affect install; just don't depend on bare `python -m graphify.serve` — use `graphify-mcp.exe` (§5). |

---

## 12. Keeping the graph fresh

- **Manual / after edits:** `graphify update .` — incremental re-extract of changed **code** files,
  no LLM, no cost. Faster than a full `graphify .`.
- **Full rebuild:** `graphify .` (use after large refactors; `--force` if the rebuild has fewer nodes
  than before and you want to overwrite anyway).
- **Auto on commit (optional):** `graphify hook install` adds git post-commit/post-checkout hooks
  (AST-only, no API cost).
- **Stop-hook integration (optional):** a Claude Code `Stop` hook can dispatch `graphify update .` in
  the background when a planning session completes (see this repo's `.claude/hooks/stop.sh`/`stop.ps1`).
- **Always restart Claude Code after a large rebuild** so the MCP server reloads `graph.json`.

---

## 13. Optional: enable docs / images / richer semantics (costs money)

Only if you want documentation, PDFs, or images in the graph:

1. Set an LLM backend key (any one): `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (+ optional
   `OPENAI_BASE_URL` for self-hosted), `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`,
   `MOONSHOT_API_KEY` (Kimi), or `OLLAMA_BASE_URL` (local, no key).
2. Remove the doc/image excludes from `.graphifyignore`.
3. Rebuild: `graphify .` (or headless `graphify extract . --backend ‹claude|openai|gemini|ollama|…›`).
   Use `--mode deep` for more aggressive inferred edges. This **calls a paid API** and incurs cost.
4. Optionally name communities: `graphify label .`.

---

## 14. Best practices for future automated setups

- **Default to code-only/offline.** It's free, deterministic, and needs no secrets — perfect for
  unattended Claude Code setup. Layer in LLM extraction later only if a project needs it.
- **Idempotent ignore-then-build loop.** The reliable build recipe is: write `.graphifyignore` →
  `graphify .` → if it complains about N docs/images, widen the ignore → repeat until `0 docs/images`.
- **Always use the full `graphify-mcp.exe` path in `.mcp.json`** on Windows; don't assume PATH.
- **Treat "restart Claude Code" as a required step**, not optional — it's the #1 reason a fresh graph
  or new server "doesn't show up."
- **Gitignore `graphify-out/`** and rebuild per-machine; don't commit multi-MB generated graphs unless
  you deliberately want team sharing.
- **Run `graphify install --project`** so the skill + hooks travel with the repo (project-scoped), not
  just your user profile.
- When **replacing** another graph tool, do the removal (§10) in the same change and **restart** to
  fully unbind the old server.

---

*Generated from the `botnexhealth` Graphify setup. Update the `‹angle-bracket›` placeholders for each new
repo; everything else is repo-agnostic.*
