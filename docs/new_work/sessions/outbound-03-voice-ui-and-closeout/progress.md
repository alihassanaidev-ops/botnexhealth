# Progress — Plan 03 Voice UI/API + closeout

## Session — 2026-07-04/05 (findings-first; auto-approve for implementation)

### P0/P1 — Findings + product decisions ✅
4 parallel graphify-first research agents → `findings.md`. Decisions (user): **XC-1b = Option A (split)**,
**V-8 = API-first**. Confirmed V-5 (→ Plan 11) and V-9 (→ infra/Plan 10) correctly deferred.

### P2 — XC-1b timeout policy ✅
Retell POST error re-classification (Retell has no idempotency key, A-4):
- `retell_outbound_client.py`: new `RetellAmbiguousError`; **timeout/network → ambiguous** (was transient);
  **5xx stays transient** (retry); **4xx permanent**.
- `voice_node_executor.py`: catch `RetellAmbiguousError` → fail run, **no retry** (no re-raise), and **leave the
  P9 claim INITIATING (blocking)** with `error_message` — NOT marked FAILED, so a task redelivery is still
  blocked (at-most-once; the call may have been placed). 5xx keeps V-6 retry + marks claim FAILED.
- Tests: new `tests/unit/test_retell_outbound_client.py` (5: timeout/transport→ambiguous, 5xx→transient,
  4xx→permanent, 200→result) + executor ambiguous test (fail/no-retry/claim-stays-INITIATING).

### P3 — disconnection_reason threading ✅ (F-3, low risk)
Raw provider `disconnection_reason` now flows webhook → task → attempt row (3 edits; optional kwarg at each
layer, `stamp_attempt_outcome` already supported it). `retell/webhooks.py` (enqueue kwargs),
`tasks/automation_workflow.py` (`resume_voice_outcome` + `_async` signatures). Integration assertion added.

### P4/P5 — V-8 API (API-first) ✅
- `voice_attempt_recorder.py`: `list_voice_attempts(...)` read helper (institution-scoped, run/location/status
  filters, newest-first) — keeps all `WorkflowVoiceAttempt` access on one seam.
- `src/app/api/routes/outbound_voice.py` (new): `/api/outbound-voice`
  - **profiles CRUD** (POST/GET-list/GET/PATCH/DELETE), gate `get_current_institution_or_location_admin`;
    institution_id from auth (never body); 409 on the one-active-profile-per-location unique index.
  - **attempts drill-down** GET, gate `get_current_institution_or_location_user`; masked numbers only.
- Registered in `main.py` (import + `include_router(..., prefix="/api")`).
- RBAC matrix: classified all 6 routes in `test_rbac_route_matrix.py` (5 admin, 1 institution/location user).
- Tests: `tests/unit/test_outbound_voice_routes.py` (11: response mappers, create 201/409, 404 wrong-institution,
  PATCH exclude_unset, delete, list filters, attempts delegation) + real-Postgres integration
  (unique-active-profile constraint + `list_voice_attempts` run/status filtering).

### Result
**1385 unit + 12 integration pass** (0 failures). Single Alembic head unchanged (`20260708_voice_data_model` —
no schema change this session). No caps introduced.

### Deferred (justified, see findings F-4)
- **V-5** voice metering → **Plan 11** (M-1). **V-9** per-clinic Retell workspace/BYO-SIP → infra/**Plan 10**.
- **V-8 FE** (React) — fast follow; mirrors `LocationAdminPanel` + `CampaignDetail` + `RevealablePhone`. Contract frozen.
- `calls`→run linkage columns — no consumer needs them; skipped.
