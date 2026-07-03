---
name: Refactor Safely
description: Plan and execute safe refactoring using Graphify dependency analysis
---

## Refactor Safely

Use the **Graphify** knowledge graph (server: `graphify`) to plan and execute refactoring with confidence.

> Note: Graphify analyzes and navigates the graph but does **not** auto-apply edits (no rename/dead-code mutation tool). Use the graph to scope the change, then apply edits with the normal Edit tools.

### Steps

1. Use `get_node` / `get_neighbors` on the target symbol to map exactly what it touches.
2. Use the CLI `graphify affected "Symbol"` (reverse traversal) to find every dependent before you change it — this is your blast radius and rename checklist.
3. For renames, drive the edit list from `affected` + a confirming `Grep`, then apply edits and update call sites.
4. To find likely dead code, look for nodes with no inbound `call`/`import` edges via `query_graph` / `get_neighbors`.
5. After changes, run `graphify update .` to refresh the graph, then re-check `affected` to confirm nothing dangles.

### Safety Checks

- Always enumerate dependents (`affected`) and cross-check with `Grep` before a rename — module-attribute call sites can hide.
- Use `shortest_path` to confirm you are not severing a critical path between two subsystems.
- `god_nodes` flags high-degree hubs — refactor those with extra care.
