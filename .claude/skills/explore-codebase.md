---
name: Explore Codebase
description: Navigate and understand codebase structure using the Graphify knowledge graph
---

## Explore Codebase

Use the **Graphify** MCP tools (server: `graphify`) to explore and understand the codebase.

### Steps

1. Run `graph_stats` to see overall codebase metrics (nodes, edges, communities).
2. Run `god_nodes` to find the high-degree hub symbols — the architectural centers.
3. Use `get_community` to inspect a cluster/module in detail.
4. Use `query_graph` with a natural-language question (BFS for broad context) to find specific functions or classes.
5. Use `query_graph` with `mode="dfs"` and `context_filter` (e.g. `['call']`, `['field']`, `['import']`) to trace relationships, and `get_neighbors` to expand a node's direct connections.
6. Use `shortest_path` between two symbols to see how two parts of the system connect.

### Tips

- Start broad (`graph_stats`, `god_nodes`) then narrow with `query_graph` / `get_node`.
- Use `get_neighbors` on a file or class node to see what it calls, imports, and defines.
- CLI equivalents for ad-hoc exploration: `graphify query "..."`, `graphify explain "Symbol"`, `graphify path "A" "B"`.
