---
name: Debug Issue
description: Systematically debug issues using Graphify-powered code navigation
---

## Debug Issue

Use the **Graphify** knowledge graph (server: `graphify`) to systematically trace and debug issues.

### Steps

1. Use `query_graph` with a natural-language description of the symptom to find related code.
2. Use `get_neighbors` and `query_graph` with `context_filter=['call']` to trace call chains in and out of the suspect symbol.
3. Use `query_graph` with `mode="dfs"` to follow a full execution path through the suspected area, or `shortest_path` to connect a trigger to the failing symbol.
4. Use `git diff` / `git log` to check whether recent changes touched these nodes (recent changes are the most common cause).
5. Use the CLI `graphify affected "Symbol"` to see what else depends on a suspect symbol (reverse traversal / blast radius).

### Tips

- Check both callers and callees to understand the full context.
- If the graph looks stale relative to the working tree, refresh first: `graphify update .`.
