# Task Plan — Plan 03 Voice: UI/API + closeout (V-8, XC-1b, follow-ons)

**Branch:** `ali/phase-2` (@ `d710e11` — V-4 + P9 committed) · **Alembic head:** `20260708_voice_data_model`
**Mode:** findings-first. Proceed to code ONLY where it makes product sense; route product decisions to the user.

## Scope (from the register, post V-4/P9)
| Item | Nature | Disposition (pre-findings) |
|---|---|---|
| **V-8** Voice UI/API — outbound-profile CRUD + call-attempt drill-down + readiness | Build (unblocked by V-4) | **Primary.** Confirm API-first vs full FE via findings. |
| **XC-1b** timeout idempotency residual | **Product decision** | Frame options, route to user. Don't guess. |
| **disconnection_reason threading** (webhook → attempt row) | Small build | Likely yes (column exists). Confirm via findings. |
| `calls`→run linkage columns | Optional | Only if a real consumer needs it. Likely skip. |
| **V-5** voice metering | Deferred → Plan 11 | Confirm boundary; do NOT build here. |
| **V-9** per-clinic Retell isolation (BYO-SIP) | Deferred, non-cap scale | Confirm it needs infra/product decision; do NOT build here. |

## Phases
- **P0 — Findings (BLOCKING).** API/router + schema + RBAC patterns for existing outbound features;
  FE dashboard structure (does outbound/voice UI exist?); webhook disconnection_reason path; V-5/V-9 boundaries.
  Write `findings.md`. **Decide what actually makes sense to build.**
- **P1 — Plan + product decisions.** Propose the concrete build set; get user sign-off on XC-1b timeout
  semantics and API-first-vs-FE scope before coding.
- **P2..Pn — Implement** the agreed slices (verified increments, tests each). TBD after P0/P1.

## Guardrails
- No caps/limits (product constraint). Compliance/consent correctness > approximation.
- Additive; single Alembic head; RLS on any new table; every increment tested; full unit green before commit.

## Findings outcome (see findings.md)
- **Build now:** V-8 backend API (profiles CRUD + attempts drill-down) + disconnection_reason threading (F-3, 3 edits, low risk).
- **Route to user:** XC-1b timeout policy (compliance); V-8 API-first vs full-stack (recommend API-first).
- **Defer (justified):** V-5 → Plan 11 (M-1); V-9 → infra/Plan 10; `calls`→run linkage (no consumer — skip).
- **FE:** fast follow after API contract frozen (small–medium; mirrors LocationAdminPanel + CampaignDetail + RevealablePhone).

## Decisions (user, 2026-07-04)
- **XC-1b = Option A (split).** Retell POST error classification: **5xx → transient (retry via V-6)**;
  **timeout/network → terminal, NO retry** (call may have been placed → never double-dial); **4xx → permanent** (as today).
  *P9 interaction:* a timeout must NOT mark the claim FAILED (a FAILED claim wouldn't block a redelivery re-dial, and the
  call MAY have gone out). Timeout path = fail the run + **leave the claim INITIATING (blocking)** + record error_message.
  New `RetellAmbiguousError` in the client for timeout/network.
- **V-8 = API-first.** Backend endpoints now; React UI is a fast follow (separate pass).

## Implementation phases
- **P2 — XC-1b.** `retell_outbound_client.py`: timeout/network → `RetellAmbiguousError` (was Transient); 5xx stays Transient.
  Executor: catch ambiguous → fail run, no retry, keep claim INITIATING (don't mark FAILED). Update client + executor tests.
- **P3 — disconnection_reason threading (F-3).** webhook adds kwarg → `resume_voice_outcome`/`_async` thread optional
  `disconnection_reason` → existing `stamp_attempt_outcome`. Extend integration test assertion.
- **P4 — V-8 API.** `list_voice_attempts` read helper on the recorder seam; new `src/app/api/routes/outbound_voice.py`
  (profiles CRUD, gate institution_or_location_admin; attempts drill-down GET, gate institution_or_location_user); register
  in `main.py`; inline Pydantic schemas; 409 on the one-active-profile-per-location unique index.
- **P5 — V-8 API tests.** CRUD + filters + RBAC (extend `test_rbac_route_matrix.py`); real-Postgres where it adds value.
- **P6 — notate + commit** (progress/report/register; graphify update).

## Status — COMPLETE (see progress.md)
- P0 Findings ✅ · P1 decisions ✅ · P2 XC-1b ✅ · P3 disconnection_reason ✅ · P4 V-8 API ✅ · P5 tests ✅
- 1385 unit + 12 integration pass. FE (V-8 UI) is the remaining fast-follow. V-5/V-9 deferred.
