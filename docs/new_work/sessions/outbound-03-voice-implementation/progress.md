# Progress — Phase 3 (Outbound Voice) Implementation

## Session — 2026-07-04 (A-5 signed off; auto-approve)

### Increment 1 — Outcome-feedback loop core ✅ (P1, P2, P3, P4, P5, P8, N-1..N-5)
The defining ~65% gap. Fire-and-forget → wait-for-outcome with an **external-event resume**.
- **P8** `retell_outbound_client.py` — mockable `RetellOutboundClient.create_phone_call` returning
  `RetellCallResult(call_id)`, with `RetellTransientError`/`RetellPermanentError` classification (timeout/5xx =
  transient; 4xx = permanent). Retell has no idempotency key (A-4) — noted.
- **P1** Executor now uses the client and **captures `retell_call_id`**, storing it on the attempt
  (`complete_step(result_metadata={"retell_call_id": ...})`, or `mark_step_awaiting_outcome` when parking).
- **P3** Executor classifies errors: transient → re-raise (Celery task retries) until `node.max_attempts`, then
  fail; permanent → fail the run. Wires the previously-unused `max_attempts`.
- **P2** `SendVoiceNode.wait_for_outcome` flag; on a placed call the executor returns a `VoiceParked` signal →
  the dispatcher parks the run WAITING with a **safety-timeout timer** (`step_dispatcher.py`). New third
  resume shape in `resume_after_timer`: a placed-and-parked voice step **advances PAST** (never re-dials;
  `_CALL_PLACED_AWAITING` marker distinguishes it from a quiet-hours hold). Removed the dead voice fallback (N-1).
- **P4** `voice_outcome.py` maps Retell `disconnection_reason` → normalized `call_outcome`
  (no_answer/busy/voicemail/answered/transferred/failed/unknown — from the researched enum). The Retell
  post-call webhook, for outbound calls, maps the outcome and enqueues `resume_voice_outcome` (correlated by
  `retell_call_id`; no-ops for fire-and-forget/non-campaign calls).
- **P5** New `resume_voice_outcome` task: finds the parked step by `retell_call_id`, cancels the safety timer,
  writes `call_outcome` into run context, and resumes → a downstream `ConditionNode` branches (no ConditionNode
  schema change). Timer-vs-webhook race is at-most-once via `run.status==WAITING` guard.
- **Tests:** rewrote voice executor unit tests (mock the client; cover call_id capture, park, transient re-raise,
  exhausted-attempts, permanent fail). New real-Postgres integration test: park→resume advances past + branches
  on `call_outcome`. **1344 unit + 9 integration pass.** No migration needed (uses `result_metadata` JSON).

### Increment 2 — V-3 consent basis ✅ (committed `450d01e`)
- `ConsentBasis` enum + `ConsentRecord.basis` column + migration `20260707_consent_basis` (idempotent).
- `record_consent`/`record_consent_identity` accept `basis`; the callback auto-consent records `EXPRESS`.
- `ComplianceGateService.check` threads `content_class`; `_resolve_consent` enforces the matrix
  (sales/marketing → express_written; recall → express; care/unset → any; NULL basis = implied). `ComplianceGate`
  protocol + `NoOpComplianceGate` accept `content_class`. Dispatcher passes the workflow's content_class.
- Tests: basis allow/block per content class. **1348 unit + 9 integration pass.**

### Delivered this session (committed)
Outcome-feedback loop (P1/P2/P3/P4/P5/P8) + consent basis (P6/V-3) — the two defining pieces (~65% of the gap).
Plan 03 now ≈ **70–75%** (was ~35%): outbound voice reacts to the call outcome, branches, retries, and enforces
content-class-aware consent.

## Session — 2026-07-04 (V-4-full: dedicated voice data model)

### Increment 3 — V-4-full voice data model ✅
Two durable tables give outbound voice its own system of record (was: only
`retell_call_id` on the generic step `result_metadata`).
- **Models** `src/app/models/outbound_voice.py`:
  - `OutboundVoiceProfile` (`outbound_voice_profiles`) — per-location outbound config
    (`retell_agent_id`, `retell_from_number`, `retell_llm_id`, `display_name`, `is_active`,
    `config` JSONB). Partial-unique on `location_id WHERE is_active` (one active profile/loc).
  - `WorkflowVoiceAttempt` (`workflow_voice_attempts`) — one row per placed call: run/step/attempt
    link, `retell_call_id` (partial-unique WHERE NOT NULL), masked to/from numbers, lifecycle
    `status` (initiating/placed/awaiting_outcome/completed/failed), `dial_outcome`,
    `disconnection_reason`, `error_message`. `VoiceAttemptStatus` enum + `VOICE_DIAL_OUTCOMES`.
  - Registered in `models/__init__.py`.
- **Migration** `20260708_voice_data_model` (head; off `20260707_consent_basis`) — idempotent
  `CREATE TABLE/INDEX IF NOT EXISTS`, RLS via the same `_rls_expr`/`_enable_rls` helper as
  `20260702_auto_workflow_core`, + `nexhealth_app` grants. No-op on fresh deploy (create_all
  already builds the tables from model metadata); real create on existing deploys.
- **Recorder** `src/app/services/automation/voice_attempt_recorder.py` — shared seam:
  `resolve_outbound_voice_profile` (override-with-fallback), `record_placed_attempt`
  (masked, PHI-safe), `stamp_attempt_outcome` (correlate by `retell_call_id`, no-op if absent).
  This is also the home for P9's future committed `initiating` pre-POST insert.
- **Wiring** `voice_node_executor.py` — resolves the profile (profile agent/number override the
  node/location defaults; absent/inactive = unchanged behavior) and records a
  `WorkflowVoiceAttempt` row on every successful placement (AWAITING_OUTCOME when
  `wait_for_outcome`, else PLACED). `tasks/automation_workflow.py::resume_voice_outcome` stamps
  the row COMPLETED + `dial_outcome` alongside writing `call_outcome` to run context.
- **Tests:** +4 executor unit tests (profile override, blank-profile fallback, attempt-row
  recording for placed + awaiting); +1 real-Postgres integration test (record → stamp → unknown
  no-op → profile resolve, under RLS). **1352 unit + 10 integration pass.** Migration verified by
  the integration chain running to head against real Postgres.
- **Unblocks:** P9 (committed claim now has its table), V-8 (attempt/outcome history + profile CRUD).
- **Scope note:** failures still recorded on the step ledger (attempt rows = *placed* calls only);
  raw `disconnection_reason` threading from the webhook into the attempt row is an easy follow-on
  (column exists, resume currently stamps `dial_outcome` only).

### Increment 4 — P9 crash-safe committed idempotency claim ✅
Closes the crash-**between-Retell-POST-and-task-commit** tail (the one double-dial vector
`already_sent` couldn't cover: on that crash the step never COMPLETED, so redelivery re-dialed).
- **Transaction trace (findings):** dispatch tasks use `async with get_system_db_session(...)` +
  explicit `await session.commit()` (no `session.begin()` block); app sessionmaker is
  `expire_on_commit=False` (`database.py:276`). So a **mid-flow commit on the shared session** is
  compatible and keeps `run`/`step` usable. Chosen over a separate claim-session to avoid a
  cross-session FK-visibility problem (the step row isn't committed when the claim is written).
- **Mechanism** (`voice_attempt_recorder.py` + `voice_node_executor.py`):
  - Before the POST: `claim_voice_attempt` inserts an `INITIATING` `WorkflowVoiceAttempt` (masked,
    no `retell_call_id`) and the executor **commits** it — durable pre-POST claim.
  - Re-entry guard: `voice_send_already_claimed(run, step)` — a committed **non-FAILED** claim →
    skip the dial (at-most-once). Sits alongside `runtime.already_sent`.
  - Success → `mark_attempt_placed` (→ PLACED/AWAITING + `retell_call_id`); a crash before the task
    commit leaves the claim INITIATING, which still blocks a re-dial on redelivery.
  - Transient/permanent error → `mark_attempt_failed` (→ FAILED) + **commit**, so a V-6 retry sees
    no active claim and re-dials. **V-6 semantics unchanged.**
- **Tests:** +4 executor unit (claim committed pre-POST, skip-on-existing-claim, transient→FAILED,
  permanent→FAILED); +1 real-Postgres integration (committed claim blocks re-dial; FAILED releases).
  **1356 unit + 11 integration pass.** No migration (uses the V-4 table).
- **Documented residual (NOT silently changed):** a *timeout* where Retell actually placed the call
  but the response was lost will still re-dial on the V-6 retry (`mark_attempt_failed`→re-dial). This
  is the provider-no-idempotency-key gap (A-4, = XC-1b), which needs a product decision on timeout
  semantics (treat timeout as terminal / at-most-once?) — flagged, not guessed. P9 does not make it
  worse than today; it closes the *crash* tail specifically.

### Remaining (staged with honest reasons — NOT implemented, see ambiguity-review A-6/A-8 + register)
- **P7 disclosure — spoken-opt-out→suppression: BLOCKED (A-8).** Routing a patient's spoken "stop" into a VOICE
  suppression requires knowing how the Retell post-call analysis surfaces a DNC intent (tag/field shape) — not
  confirmed in code or docs. Per the rules, not implemented; needs a sample analyzed payload. (The disclosure
  TEXT is already injected; prompt-speaks-it verification via get-retell-llm is brittle → follow-up.)
- **P9 crash-safe committed claim:** correct form needs the dedicated `workflow_voice_attempts` table (V-4-full)
  with a committed `initiating` state before the POST; the common double-dial vectors (redelivery / re-advance /
  hold-resume) are already covered by `already_sent`, so this narrow tail is a documented follow-up.
- **V-4-full** (outbound_voice_profiles + workflow_voice_attempts + calls linkage) and **V-5** (voice metering →
  Plan 11) — follow-ups.
