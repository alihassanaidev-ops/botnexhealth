# Progress: QA-prep — 3 verification fixes

## 2026-07-13 — all 3 fixed in one pass

### Fix 1 — Email bounce/complaint webhook resolves institution (P1 correctness)
- `src/app/api/routes/email_compliance.py` — `_institution_from_tags` parses BOTH list-of-{name,value}
  (the shape the executor sends) and dict, never 500s; no-scope events route to email_hash resolution;
  removed now-unused `hash_for_logging` import.
- `src/app/tasks/email_compliance.py` — new `suppress_email_for_recipient` task: resolves institution(s)
  from `consent_records.email_hash` under a read-only SUPER_ADMIN system session (existing `main.py`
  pattern → satisfies `app_rls_is_super_admin()`, reads cross-tenant, NO RLS migration), then fans out the
  existing least-privilege per-institution `suppress_email_consent`.
- Tests: list-shaped tags scoped; no-scope routes to resolver; fan-out per institution; no-op when no record.

### Fix 2 — Voice + SMS campaign attribution (P1 reporting)
- Voice: `voice_node_executor.py` stamps `workflow_id` into Retell metadata; `retell/webhooks.py` passes it.
- SMS: migration `20260712_sms_workflow_attribution` (nullable `workflow_run_id`/`workflow_id` on
  `sms_history_logs`, idempotent); `sms_service.send_sms` stamps them; `sms_node_executor` passes run ids;
  `twilio_webhooks.sms_status` carries them into the usage event.
- Tests: voice metadata assert; SMS send kwargs assert; tenant-scope allowlist line 291→300.

### Fix 3 — Plan 08 staff DNC admin UI (frontend, subagent, CTO lane)
- `nexus-dashboard-web`: `lib/do-not-contact-api.ts`, `pages/DoNotContactAdmin.tsx`, `types/index.ts`
  (`DncRecord`), `router.tsx` route `/institution-admin/do-not-contact` (INSTITUTION_ADMIN),
  `components/app-sidebar.tsx` nav, `test/do-not-contact-api.test.ts`.
- Release re-prompts for the full phone (masked value can't hash-match) — correct given the contract.
- ⚠️ NOT build-verified (node_modules root-owned, EACCES) → needs `tsc`/vitest.

### Cleanup
- Removed satisfied `TODO(Plan 03)` in `usage_metering_service.py`.

## Test results
- 1479 unit tests pass. Only pre-existing failures: 3 Redis-down appointment-route tests (unrelated).
- Migration chain: single head `20260712_sms_workflow_attribution`. NOT applied (DB 5433 down); trivial ADD COLUMN.

## Doc sync
- v2 report.md — added a 2026-07-13 "Post-verification fixes" banner (marks the 3 findings closed).
- v3 report.md — findings remain as the record of what was found; this session is the fix log.

## Status
**Complete** ✅
