## Repository context

New to this codebase? Start with **[docs/REPOSITORY_CONTEXT.md](docs/REPOSITORY_CONTEXT.md)** -
the onboarding & integration guide. It explains the product, the multi-tenant
hierarchy (InstitutionGroup -> Institution -> Location), the full RBAC permission
model, how each clinic's Retell voice agent is provisioned and bound, the
appointment-booking flow, the automation/GoTracker integration map, the
Twilio/phone-number model, and a consolidated external-services & config
reference. It links out to the deeper docs
(`docs/ARCHITECTURE.md`, `docs/NEXHEALTH.md`, `docs/SECURITY.md`, ...).

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Documentation upkeep

When changing core workflows, tenant/security behavior, PMS/Retell/Twilio/GoTracker integrations, automation engine behavior, deployment flow, or public operating assumptions, update the relevant docs in the same change. Keep sprint status and task progress in ClickUp, not repo docs.

## Agent skills

### Issue tracker

Issues and work items are tracked in ClickUp via the ClickUp MCP, not GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Triage roles map to this repo's ClickUp status vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

This repo uses a single-context domain-doc layout. See `docs/agents/domain.md`.
