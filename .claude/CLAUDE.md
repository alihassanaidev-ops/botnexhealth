# Claude Code Instructions

> Central orchestration. **Scan the Map, pull only the sections the task needs.**

## Map — scan & jump

| § | Topic | Use when |
|---|---|---|
| 0 | Repository Context | Always · single-app repo + graph location |
| 1 | Core Principles | Always · 3 rules |
| 2 | Entry Flow | Every prompt · classify + route |
| 3 | Knowledge Graph | Code exploration or code questions |
| 4 | Planning with Files | 3+ steps · multi-file · architectural |
| 5 | Skills | Structured sub-procedure inside a flow |
| 6 | Model Selection | Every turn · right-size |
| 7 | Execution | Verify, subagents, autonomous scope |
| 8 | Efficiency | Context & token tips |
| 9 | Assets | Pointers to local templates / skills / hooks |

Each section is self-contained. Skip the ones that don't apply.

---

## 0. Repository Context

**This is a single application** — the `botnexhealth` backend (`src/app/…`) plus a `nexus-dashboard-web/` frontend, with `alembic/` migrations, `tests/`, `infra/`, and `scripts/`. It is one git repository rooted here.

### Knowledge graph — Graphify

The code graph is built by **Graphify** (`graphifyy` on PyPI, CLI `graphify`). The graph lives at `graphify-out/graph.json` (gitignored output dir), and is served to Claude Code over MCP by the `graphify-mcp` server registered in `.mcp.json`. See §3 for how to query, build, and refresh it.

- **Code-only build.** A `.graphifyignore` excludes docs/markdown/images so the build stays **AST/tree-sitter only — fully offline, no LLM API key, no cost**. To also ingest docs/images for richer semantic edges, set an LLM key (e.g. `ANTHROPIC_API_KEY`) and remove those excludes.
- **The MCP server binds the graph at Claude Code startup** and serves `graphify-out/graph.json` for the whole session. After a large rebuild, restart Claude Code so the server reloads the fresh graph.

---

## 1. Core Principles

- **Simplicity first** — touch minimal code.
- **Root causes only** — no temporary fixes.
- **Minimal impact** — change only what's necessary.

---

## 2. Entry Flow

### Step 1 — Classify

| Class | Shape | Example |
|---|---|---|
| **A · Q&A** | No code change | "How does X work?" |
| **B · Trivial** | 1-line fix, typo, rename | "Fix typo on line 42" |
| **C · Scoped build** | Feature / fix in 1–2 modules | "Add retry to email agent" |
| **D · Architectural** | Multi-module, structural, strategic | "Design the lead-routing pipeline" |

**Mixed prompt → split.** Different pieces → different flows → different models (§6).

### Step 2 — Route

| Flow | Sequence | Session folder | Model (§6) |
|---|---|---|---|
| **A** | Graph → answer | No | Haiku / Sonnet |
| **B** | Locate → edit → smallest check | No | Haiku / Sonnet |
| **C** | Session → graph → plan → execute → verify → graph update | Yes | Sonnet · Opus for hard bits |
| **D** | Session → graph → propose → confirm → execute → verify → graph update | Yes | Opus (decision) · Sonnet (mech) |

### Step 3 — Subsystem order (inside any flow)

```
Graph → Plan → Skills / Subagents → Execute → Verify → Graph update
```

Skip steps that don't apply. **Never skip verify.**

---

## 3. Knowledge Graph

**Graphify** code graph, served over MCP as the `graphify` server (current build: ~5.2K nodes, ~15.9K edges, 221 communities across 447 code files). **Query BEFORE Grep/Glob/Read** for code questions. Fall back to Grep/Glob/Read only when the graph can't answer.

### MCP tools (server: `graphify`)

| Need | Tool |
|---|---|
| Search / find symbol / context | `query_graph` (BFS broad context, DFS to trace a path; supports `context_filter` e.g. `['call','field']`, `token_budget`, `depth`) |
| Single entity details | `get_node` |
| Direct connections | `get_neighbors` |
| Community / cluster | `get_community`, `god_nodes` (high-degree hubs) |
| Shortest path between two symbols | `shortest_path` |
| Graph size / stats | `graph_stats` |
| PR analysis | `list_prs`, `get_pr_impact`, `triage_prs` |

### CLI (run with `~/.local/bin` on PATH; `graphify` + `graphify-mcp` exes live there)

```bash
graphify .                 # full build → graphify-out/graph.json (AST/offline for code)
graphify update .          # incremental re-extract of changed code files (no LLM)
graphify cluster-only . --no-label   # regenerate GRAPH_REPORT.md / communities offline
graphify query "..."       # CLI query (same engine as the MCP query_graph tool)
graphify explain "Symbol"  # plain-language explanation of a node + neighbors
```

### Verified locally (2026-06-25)

- `graphifyy` installed via `uv tool install "graphifyy[mcp]"`. Two exes: `graphify`, `graphify-mcp` at `C:\Users\AliHa\.local\bin\` (not on system PATH — `.mcp.json` references the full exe path).
- MCP server registered in `.mcp.json` → `graphify-mcp graphify-out/graph.json` (stdio). MCP handshake verified; exposes the tools above.
- Output dir `graphify-out/` is gitignored. Graph build is **code-only** (`.graphifyignore` excludes docs/images) → no LLM, no API cost.
- **No auto-update hook on every edit.** Refresh deliberately: `graphify update .` (fast, code-only). The Stop hook dispatches `graphify update .` in the background when all session phases complete. After a big rebuild, restart Claude Code so the MCP server reloads the new graph.

### Notes / limitations

- **Community names are placeholders** (`Community N`) because labeling is run with `--no-label` to stay offline. Run `graphify label .` with an LLM key set to get human-readable cluster names.
- **Docs/images are not in the graph** by default (excluded for zero-cost). Add an LLM key and drop the `.graphifyignore` doc/image excludes to ingest them.
- **HTML viz skipped** when the graph exceeds ~5000 nodes; `GRAPH_REPORT.md` is still generated. Raise `GRAPHIFY_VIZ_NODE_LIMIT` if you need `graph.html`.

---

## 4. Planning with Files

### Required for
3+ step task · multi-file edit · architectural (Flow D) · cross-source research.

### Skip for
Flow A · Flow B.

### Session folder — `docs/sessions/<kebab-title>/`

New workstreams live under `docs/sessions/`. Each session folder contains **exactly three files**:

| File | Purpose |
|---|---|
| `task_plan.md` | Phases, status, decisions |
| `findings.md` | Research notes, hotspots, source citations |
| `progress.md` | Session log, test results |

Templates: `.claude/planning-with-files/templates/`.

Active sessions:
- `docs/sessions/reporting-llm/` — Reporting LLM v1 (complete; 14 PRs landed)
- `docs/sessions/punch-list-v1/` — 13-item client ticket (in progress; 4 slices shipped)

Historical archive (pre-convention): `planning/` — earlier `reporting_llm_v1/`, `punch_list_v1/`, `graph_setup_v1/`, plus root `task_plan.md` / `findings.md` / `progress.md` for M4/M5 verification. Treat as read-only reference; do not modify.

For new workstreams, follow the three-file structure. Don't split into `session_N_<topic>.md` files — keep everything inline in `progress.md` (use a "Slice N" or "Session N" subheading).

### Rules

1. `task_plan.md` first.
2. **2-Action rule** — after every 2 read/search ops, append to `findings.md`.
3. Re-read plan before major decisions.
4. Update after each phase — status, errors, files.
5. Log ALL errors. Never repeat a failed action; mutate approach.

### 3-Strike on repeated failure

`1 diagnose → 2 alternative → 3 rethink → (>3) escalate`.

### End of session — REQUIRED

```bash
graphify update .
```

Re-extracts changed code files into `graphify-out/graph.json` (code-only, no LLM). On successful Stop (all phases complete), the Stop hook dispatches this in the background — but run it manually if you suspect drift. After a large rebuild, restart Claude Code so the `graphify` MCP server reloads the fresh graph.

### Resume an existing session

Read all 3 files → `git diff --stat` → update → proceed.

### Pause / halt a session (escape hatch)

The Stop hook auto-continues incomplete sessions via `followup_message`. To stop cleanly mid-task:

- Mark all phases `**Status:** complete` in `task_plan.md`, or
- Delete or rename `task_plan.md`.

Either breaks the auto-continue loop. The session folder itself can stay.

---

## 5. Skills

`.claude/skills/*.md` are structured **sub-procedures inside a flow**, not replacements for it. Read the skill file, follow its steps. Skills lean on §3 (Graph) heavily.

| Skill | When |
|---|---|
| `debug-issue` | Trace cause of a break |
| `explore-codebase` | Understand unfamiliar module or flow |
| `refactor-safely` | Rename, extract, remove dead code |
| `review-changes` | Pre-commit or pre-PR |

---

## 6. Model Selection

**Prefer smaller. No Opus unless reasoning demands it.**

| Model | Use for |
|---|---|
| **Haiku** | Lookups, formatting, simple renames, reading |
| **Sonnet** | **Default.** Implementation, typical fixes, moderate reasoning |
| **Opus** | Architecture, ambiguous problems, deep debugging — last resort |

**Mixed prompts → split models per piece.** Don't run the whole thing on Opus because one slice is architectural.

---

## 7. Execution

- **Plan before non-trivial work** (EnterPlanMode or chat). Re-plan if execution breaks.
- **Subagents** — one task each; keep main context clean; use for parallel research & synthesis.
- **Verify** — tests + logs + behavior diff. Ask *"Would a staff engineer approve?"*
- **Elegance** — for non-trivial changes ask *"Is there a more elegant way?"* Skip for simple fixes.
- **Autonomous fixing (scoped)** — clear signals → fix. Reversible local changes only. Confirm before destructive or shared-state actions (migrations, force push, deploys).

---

## 8. Efficiency

- Graph first — one query beats 5 file reads.
- Offload wide research to subagents.
- Large findings → `findings.md`, not context.
- Surgical reads — use `offset`/`limit` on large files.
- Summarize subagent returns; never paste raw.
- Don't re-read just-edited files.

**Thinking mode** — use only for: architecture, multi-signal debugging, trade-off evaluation. Skip for: simple reads, CRUD, commands.

---

## 9. Related Assets

- `.claude/planning-with-files/templates/` — starter templates (`task_plan.md`, `findings.md`, `progress.md`).
- `.claude/skills/*.md` — structured sub-procedures.
- `.claude/hooks/*` — harness-enforced nudges: Map reminder on prompt submit, plan context on tool use, progress reminder after edits, auto-continue + background `graphify update .` on stop. Wired in `.claude/settings.json`.
- `.mcp.json` — registers the `graphify` MCP server (stdio, `graphify-mcp graphify-out/graph.json`).
- `.graphifyignore` — keeps the graph build code-only (excludes docs/images so it stays offline / zero-cost).
- `planning/` — active workstream session folders.
