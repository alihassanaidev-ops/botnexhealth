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

### Remaining increments (this session, in progress)
- **P6** V-3 consent basis (column + gate content-class threading + validator hard-block + matrix — A-5 signed off).
- **P7** disclosure enforcement (prompt verification via get-retell-llm) + spoken-opt-out→voice suppression.
- **P9** crash-safe idempotency (committed-before-send claim; A-4 = no provider key).
- Deferred: V-5 voice metering → Plan 11.
