---
name: Review Changes
description: Perform a structured code review using Graphify impact analysis
---

## Review Changes

Perform a thorough, risk-aware code review using the **Graphify** knowledge graph (server: `graphify`).

### Steps

1. Run `git diff --stat` to get the changed surface, then `graphify update .` so the graph reflects the working tree.
2. For each changed symbol, run the CLI `graphify affected "Symbol"` (reverse traversal) to find impacted dependents and execution paths.
3. For each high-risk function, use `get_neighbors` / `query_graph` to locate its tests (test nodes that call it) and judge coverage.
4. Use `shortest_path` and `god_nodes` to understand the blast radius — changes near hubs carry more risk.
5. For any untested changes, suggest specific test cases.

### Output Format

Provide findings grouped by risk level (high/medium/low) with:
- What changed and why it matters
- Test coverage status
- Suggested improvements
- Overall merge recommendation
