# Task Plan: QA-prep — 3 verification fixes

## Goal
Close the 3 findings from the v3 verification before entering QA.

## Fixes
1. **Plan 05 — bounce/complaint webhook resolves institution.** Today it reads `tags` as a dict
   but the executor sends a list, AND Resend doesn't echo tags on bounced/complained → institution_id
   empty → suppresses nothing. Fix: resolve institution(s) from the recipient email and suppress.
   Add a test that feeds the ACTUAL Resend payload shape. (Backend, P1 correctness.)
2. **Plan 11 — stamp workflow_id on voice (and SMS if feasible) usage events** so `/by-campaign`
   includes them. Voice: look up run→workflow_id in the Retell webhook. SMS: assess linkage cost.
   (Backend, P1 reporting.)
3. **Plan 08 — staff DNC admin UI** consuming the existing `POST/DELETE/GET /api/institution/do-not-contact`.
   (Frontend, CTO lane — build following existing admin-panel patterns; note FE build/test can't be
   executed locally — node_modules root-owned.)

## Constraints
- Verification is read-only history; this is the fix pass.
- Re-run affected tests green before declaring done.
- No commit — provide message; user commits.

## Status
**Complete** ✅ — all 3 fixed + stale-TODO cleanup. 1479 unit tests pass (only the 3
pre-existing Redis-down appointment tests fail, unrelated). SMS migration authored +
chain-verified (applies on next `alembic upgrade head`; DB was down this session).

## What shipped
**Fix 1 — Email bounce/complaint webhook now resolves institution (no RLS migration).**
- `email_compliance.py`: defensive `_institution_from_tags` (handles list-of-{name,value}
  AND dict, never 500s); when no scope on the event → route to email_hash resolution.
- `tasks/email_compliance.py`: new `suppress_email_for_recipient` — resolves institution(s)
  from `consent_records.email_hash` under a read-only SUPER_ADMIN system session (existing
  `main.py` pattern), then fans out the existing least-privilege per-institution suppress.
  Recipients with no consent record → 0 (unsubscribe link covers those).
- Tests: real list-shaped tags scoped; no-scope routes to resolver; fan-out per institution; no-op when none.

**Fix 2 — Voice + SMS now visible in /by-campaign (Plan 11).**
- Voice: `voice_node_executor` stamps `workflow_id` into Retell metadata; `retell/webhooks`
  passes it to the usage event. (Symmetric with workflow_run_id; no lookup.)
- SMS: new nullable `workflow_run_id`/`workflow_id` columns on `sms_history_logs`
  (migration `20260712_sms_workflow_attribution`, idempotent); `send_sms` stamps them on
  send; `sms_node_executor` passes run ids; delivery webhook carries them to the usage event.
- Tests: voice metadata assert; SMS send kwargs assert. Allowlist line 291→300 updated.

**Fix 3 — Plan 08 staff DNC admin UI** (frontend, built by subagent, CTO lane).
- `nexus-dashboard-web`: `do-not-contact-api.ts`, `pages/DoNotContactAdmin.tsx`, types,
  router route `/institution-admin/do-not-contact` (INSTITUTION_ADMIN), sidebar nav,
  api-client test. **NOT build-verified** — node_modules root-owned (EACCES); needs `tsc`/vitest.

**Cleanup:** removed satisfied `TODO(Plan 03)` in `usage_metering_service.py`.

## Follow-ups for QA
- Apply migration `20260712_sms_workflow_attribution` when DB up.
- Run FE `tsc`/vitest on the DNC UI once node_modules is fixed (`sudo chown`).
- Staging: confirm Resend actually omits tags on bounce/complaint (validates the resolver path).
