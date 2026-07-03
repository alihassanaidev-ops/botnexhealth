# Findings — Phase 2 Verification v2

## Orientation (P1)
- Scope read. Engine-first delivery spine: 01 engine + 12 consent → 10 provisioning +
  04 SMS + 09 data → 06 Reminder + 11 metering + 08 read-only → 03/05 → 06 rest + 07 →
  02 full + 08 full + 11 dashboards.
- Sequence doc stresses: 10 & 12 numbered last but foundational; 11 metering ships with 04.
- Plan 03 (voice) EXCLUDED from audit.
- Graph updated 2026-07-03 (exit 0).

## Baseline delta vs prior report (sessions/verification-phase2/report.md)
Prior report state (earlier same day): only 01, 06, 09 built; 08/02 partial; channels
04/05, provisioning 10, compliance 12, callback 07, usage 11 = NOT STARTED.
Since then (git log) these landed: Plan 10 provisioning, Plan 04 SMS, Plan 05 email,
Plan 02 builder UI + backend follow-ups (commit 6177641), merge from
feature/outbound-engagement-engine.

**Prior open defects to re-check against current code (validate whether fixed):**
- ENG-01 recover_stale_claims unscheduled (CRITICAL)
- ENG-05 inline enroll hardcodes UTC (HIGH)
- ENG-06 respect_quiet_hours never enforced (MED→HIGH)
- ENG-02 emergency-halt missing (HIGH)
- ENG-03/04 enrollment idempotency race (HIGH)
- TPL-01/02 template instantiate TypeError + no version persisted (CRITICAL)
- DATA-01 webhook signature silently skipped when secret unset (CRITICAL)
- DATA-04 cancellation/reschedule not handled (HIGH)
- XCUT-03 compliance gate NoOp — is Plan 12 now real & wired into dispatch?
- XCUT-04 sends were stubs — are SMS/email real now?

## Per-plan verification (P2) — see plan-NN-findings.md for full detail
(populated by subagents)
</content>
