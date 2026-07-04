# Progress — XC-1 send-time idempotency

## Session — 2026-07-04

### Research (P0) ✅
- Traced `advance()` send-node path + hold/resume + `begin_step`/`resume_run` → `findings.md`.
- **Discovered a latent bug** (from the Finding-B hold fix): after a quiet-hours hold→resume, `advance()`
  re-enters the send node and calls `begin_step(node.id)` at `attempt_number=1` again — but the hold already
  created attempt 1 → **`uq_automation_step_execution_attempt` IntegrityError**. Untested (unit mocks the
  runtime; the only integration resume test uses a WaitNode). Fixing it also delivers idempotency.

### Implement (P2) ✅
- **`runtime_service.begin_step`** now allocates the **next** `attempt_number` for `(run, step_id)` when not
  given (`max(existing)+1`). Root fix for the hold→resume collision; records each attempt distinctly.
- **`runtime_service.already_sent(run, step_id)`** — True if a COMPLETED step with a send-success result code
  (`sent`/`call_placed`) exists. Added `SEND_SUCCESS_RESULT_CODES`.
- **SMS / email / voice executors** each call `already_sent` FIRST → skip the vendor call + advance if already
  sent. Voice's bespoke P0-3 probe replaced by this shared guard (removed unused imports).

### Tests (P3) ✅
- Unit: idempotent-skip per channel (no second send, still advances); runtime `begin_step` attempt=1,
  auto-increment→2, explicit attempt honored. Updated the three executor test helpers to stub
  `runtime.already_sent=False` by default; fixed the runtime test's fake session for the new max-attempt query.
- **Integration (real Postgres):** new `test_send_step_idempotency_and_reclaim_after_hold` — proves a second
  `begin_step` on the same node does NOT collide (attempt 2) and `already_sent` flips True after a completed send.

### Verify (P4) ✅
- **1329 unit passed, 7/7 integration passed, 0 failures.** Single Alembic head unchanged (no migration needed).

### Residual (documented as XC-1b in the register — NOT closed here)
The crash-between-send-and-commit window is not closed by a same-transaction guard (the whole Celery task
commits at the end, so a crash rolls back `begin_step` too). Real fix = a committed-before-send claim (own
session) and/or a **provider idempotency key** (Resend `Idempotency-Key`; Twilio/Retell where supported). The
implemented guard closes the common redelivery / re-advance / hold-resume vectors + the latent collision.

**Nothing committed yet.**
