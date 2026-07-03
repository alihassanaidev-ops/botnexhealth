# Task Plan: Outbound 03 — Outbound Voice

## Goal
Replace `_dispatch_send_stub` for `SendVoiceNode` with a real outbound call via
Retell's `POST /v2/create-phone-call`. Reuse the single enterprise ScaleNexusAI
agent (same agent JSON that serves inbound). Fire-and-forget for v1.

## CTO directive
"For Outbound Voice, you can use the same Voice Agent JSON. Before implementation
see what API and components can be reused and what needs to be developed on top."

## Reuse vs Build (analysis result)

**Reused (no new work):**
- The enterprise agent itself (same `agent_id` serves inbound + outbound)
- `settings.retell_api_secret` (auth)
- `InstitutionLocation.retell_agent_id` (location→agent binding)
- Retell function tools (lookup_patient, book_appointment, …) — work identically on outbound
- **Webhook already persists the outbound Call row** end-to-end (`webhooks.py` L495
  handles `direction=="outbound"`, resolves patient phone from `to_number`, location,
  contact, retention). Executor therefore NEVER touches the Call table.
- httpx Bearer pattern, compliance gate, `SendVoiceNode` fields (all present)

**Built:**
- `retell_from_number` column on `institution_locations` (+ migration)
- `VoiceNodeExecutor` — places the call, returns next_node_id
- Dispatcher wiring (replace stub branch for SendVoiceNode)
- Tests

## Decisions (locked with user 2026-07-03)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | Outcome model | **Fire-and-forget (v1)** | Matches Plans 04/05, zero schema/webhook change. Call still recorded by existing webhook. Wait-for-outcome deferred to its own plan. |
| D1a | Correlation hedge | **Stamp `run.id` into Retell call `metadata`** | Costs nothing now; makes future wait-for-outcome correlation possible with no backfill. |
| D2 | From-number | **New `retell_from_number` per-location field** | Only per-tenant-correct option. Reusing twilio_from_number assumes it's imported into Retell (unconfirmed ops fact); platform number breaks per-clinic caller ID. |
| D3 | Agent priority | **Node's `retell_agent_id`** (required field) | `SendVoiceNode.retell_agent_id` is required (min_length=1); always authoritative. Passed as `override_agent_id`. |

## Slices

### Slice 1 — Schema: retell_from_number
- [ ] `InstitutionLocation.retell_from_number` (nullable String(20))
- [ ] Migration chaining from `20260703_provisioning`

### Slice 2 — VoiceNodeExecutor
- [ ] `src/app/services/automation/voice_node_executor.py`
- [ ] Resolve contact → `contact.phone` (fail if missing)
- [ ] Resolve location → `retell_from_number` (fail if missing)
- [ ] `settings.retell_api_secret` (fail if missing)
- [ ] POST create-phone-call: from/to, override_agent_id, dynamic vars {first_name, user_number}, metadata {workflow_run_id}
- [ ] Fail step + fail run on any error; complete_step("call_placed") on success

### Slice 3 — Wire dispatcher
- [ ] `step_dispatcher.py`: route `SendVoiceNode` → `VoiceNodeExecutor`
- [ ] Stub now unused for all send nodes (SMS/email/voice all live)

### Slice 4 — Tests
- [ ] No contact / not found / no phone → fail
- [ ] No retell_from_number → fail
- [ ] Retell not configured → fail
- [ ] Success → complete step, dynamic vars + metadata in payload, return next_node_id
- [ ] Retell HTTP error → fail step + fail run
- [ ] Update compliance-gate dispatcher test (patch VoiceNodeExecutor)

## Files Touched
| File | Change |
|------|--------|
| `src/app/models/institution_location.py` | +retell_from_number |
| `alembic/versions/20260703_retell_from_number.py` | New migration |
| `src/app/services/automation/voice_node_executor.py` | New |
| `src/app/services/automation/step_dispatcher.py` | Route SendVoiceNode |
| `tests/unit/test_outbound_voice_executor.py` | New |

## Current Status
**Complete** ✅ — all 4 slices shipped. Migration applied to DB (`retell_from_number`
confirmed on institution_locations). 1192 unit tests pass; only pre-existing env
failures (respx missing, Redis down) remain.
