# Progress — Outbound Safety & Compliance

## Session 1 — 2026-07-04

### Setup
- Read full `docs/new_work` scope: platform (inbound Retell dental voice agent) + Outbound
  Engagement Engine new scope. Confirmed the P0 bundle + Plan 12 semantics are traceable to
  Scope §11/§12 and Plan 12 doc.
- Product-owner exclusion recorded: no caps/limits (frequency, spend, blast-radius, concurrency).
- Graph updated (no topology changes — committed code already reflected).
- Session folder + `task_plan.md` created.

### Phase P0 — Research ✅
- 2 parallel research agents returned precise anchors → consolidated in `findings.md`.

### P0 bundle ✅ (all 3 defects fixed + tested)
- **P0-1 webhook fail-closed:** `config.py` prod guard (raise if `is_production and not nexhealth_webhook_secret`)
  + defense-in-depth 403 in `_verify_signature` when prod+empty. Verified via config construction test.
- **P0-2 email-consent identity:** `sms_privacy.hash_email/normalize_email/mask_email`; `ConsentRecord` gains
  nullable `email_hash`/`email_masked`, `phone_hash`/`phone_masked` relaxed to nullable, new email index;
  gate split into `_check_email_consent` (email identity) vs `_check_phone_consent` (SMS/voice). Migration
  `20260705_consent_email_identity` (idempotent, off head). Email-only contacts now pass with granted email consent.
- **P0-3 voice idempotency:** `VoiceNodeExecutor` probes the attempt ledger (`AutomationWorkflowStepExecution`
  completed + `result_code="call_placed"` for this (run,node)) BEFORE dialing; skips re-dial on redelivery/retry.
- Tests: updated 3 test files (voice mock, webhook mock intent, gate email→email-identity) + added 4 new tests
  (voice idempotent-skip; webhook prod-rejects; email no-email block; email-only allow). **50 passed.**
- Alembic: single linear head `20260705_consent_email_identity`.

### Plan 12 semantics ✅
- **P4 content-class + PHI validator:** new `content_compliance_validator.py` implementing the
  `ContentComplianceValidator` seam. Promotional language in an exempt class (`transactional_care`/
  `recall`) → **error** (voids TCPA/CASL exemption); high-risk PHI/financial terms in a body → **error**;
  broader clinical terms → **warning**. Wired into publish (`definition_service.publish_version`) + the
  builder `/validate` endpoint. Word-boundary matching avoids false positives. 9 tests.
- **P5 AI-voice consent/disclosure:** `VoiceNodeExecutor` injects a `compliance_disclosure` dynamic
  variable (clinic identity + automated-call disclosure + opt-out) + `clinic_name` + `ai_automated_call`
  metadata; the validator emits `ai_voice_disclosure_required` (all voice) and
  `ai_voice_marketing_needs_express_consent` (marketing-class voice). Tests updated + added.
- **P6 bilingual FR STOP** (delegated, verified): FR opt-out keywords (ARRET/ARRÊT/DESABONNER/
  DÉSABONNER/RETIRER/SUPPRIMER) + AIDE added to `twilio_webhooks.py`; tokenizer broadened to Unicode
  letters so accented forms match. 29 tests pass.
- **P7 DNC tiers:** `DoNotContact.scope` (location|institution|group, default institution, migration
  `20260706_dnc_scope`); scope-aware `SmsComplianceService.is_do_not_contact` + `set_do_not_contact`;
  gate now enforces DNC on **voice + email** too (previously SMS-only — a real hole). Tests added.
- **P8 builder surfacing:** FE audit confirmed the validation panel renders issues generically —
  the 3 new codes surface automatically (severity color + code label + node-link). **No FE change needed.**

### Phase P9 — Verification ✅
- Touched-area unit tests all green: P0 bundle (50), content validator + validation service (13),
  voice + content + FR STOP (46), gate + sms-compliance + consent-coverage (33).
- Full `tests/unit`: **1294 passed**. 12 failures + 2 collection errors are **pre-existing** — proven by
  re-running the failing files against the committed baseline (HEAD) in a throwaway worktree (same failures):
  - refresh_token_service `_encode_session()` signature bug (untouched code, Python 3.14 env),
  - `test_tenant_scope_invariant` reads source with cp1252 → chokes on non-ASCII in `audit.py` (untouched; test bug — should read UTF-8),
  - `test_rbac_route_matrix`: emergency-halt route (added in the prior Plan-12 gate session) missing from the expected matrix,
  - `respx` missing dev dep → `test_locations_routes` / `test_nexhealth_client` collection errors (P2-13).
- **Integration: 6/6 pass vs REAL Postgres** (testcontainers, `TESTCONTAINERS_RYUK_DISABLED=true`) — the
  merged migration chain including `20260705_consent_email_identity` + `20260706_dnc_scope` applies cleanly
  on a fresh DB; engine mechanics intact.
- Single linear Alembic head: `20260706_dnc_scope`.

### Result
All planned P0 + Plan-12-semantic work landed and verified. Caps deliberately excluded per product owner.

### Green-suite hardening (2026-07-04, after Items 1&2 final verification)
Cleared ALL pre-existing red so the board is 100% green before starting Item 3. Full suite now
**1325 unit passed (0 failures, 0 collection errors) + 6/6 integration**. Fixes:
- **REAL auth bug** — `RefreshTokenService._encode_session` had a bogus stacked `@classmethod`+`@staticmethod`
  (Python removed that chaining in 3.13); on 3.14 it broke `issue_token` (login/refresh). Removed the
  redundant `@classmethod`. (`src/app/services/refresh_token_service.py`.)
- **Un-masked tenant-scope finding** — `test_tenant_scope_invariant` read source as cp1252 and crashed on
  non-ASCII in `audit.py`, silently disabling the PHI-scope invariant. Fixed to `encoding="utf-8"`; that
  surfaced a stale ALLOWLIST line number (`sms_service.py` 289→291 for the `SmsHistoryLog` message_sid
  lookup — already justified: Twilio SID is globally unique, no institution context). Corrected the line.
- **RBAC matrix drift** — 6 automation routes (validate, dry-run, channel-readiness, merge-fields, versions,
  {id}/emergency-halt) were missing from `test_rbac_route_matrix`; added each to the bucket matching its
  actual auth dependency (INSTITUTION_USER / INSTITUTION_OR_LOCATION_ADMIN).
- **Stale engine tests** — `test_automation_compliance_gate` (patched the removed `step_dispatcher.SmsNodeExecutor`;
  hold tests asserted old drop-behavior) and `test_event_bus` (`workflow_run_updated` event type) — updated to
  current architecture (action-registry seam; hold-defers-not-drops; new SSE event type).
- **respx dev dep** — added `respx>=0.21` to `pyproject.toml [dev]` + installed; `test_locations_routes` and
  `test_nexhealth_client` now collect and pass.

Nothing committed yet.
