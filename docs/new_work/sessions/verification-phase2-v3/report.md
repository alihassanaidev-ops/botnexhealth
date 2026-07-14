# Phase 2 Implementation Verification (v3) — 4-Bucket Classification

**Date:** 2026-07-12
**Requested by:** CTO — "go through every session, verify exactly what's implemented, don't assume it's correct or necessary, classify into 4 buckets."
**Method:** 12 independent read-only adversarial verifier sub-agents (one per plan). Each: graphify-orient → verify claims at `file:line` → check wiring/reachability → scan/run tests → classify. Prior v2 report + register used only as **claims to challenge**. Raw per-plan returns: `findings.md`.
**Depth:** Standard (file:line + wiring + test scan; trusts the ~1400-test green baseline, spot-confirmed several suites live).
**Scope note:** Plan 05 (E-3/E-4) and Plan 09 (D-5/D-6) remainders were QA-deferred 2026-07-12 (external/staging); their deferred pieces are treated as known-open, not new findings.

---

## 1. Headline

**The system is real, wired, and largely production-grade. The "implemented but unnecessary" bucket the CTO was worried about is nearly empty — this is not a cruft-laden codebase.** Across 12 plans the only genuinely removable *code* is one dead extensibility seam in Plan 01; everything else in Bucket 3 is stale comments / doc drift.

The verification did surface **one real correctness defect the v2 report undersold** (Plan 05 bounce/complaint webhook suppresses nothing in prod) and **one real reporting gap the v2 report overstated** (voice — and SMS — are invisible in per-campaign spend). Both are fixable in hours and neither blocks an operator-driven pilot.

**Verdict for the QA gate:** proceed to QA. Fix the Plan 05 webhook before relying on automated bounce suppression; treat the rest as a tracked punch-list.

---

## 2. Per-plan 4-bucket dashboard

| Plan | Verdict | B1 Complete | B2 Incomplete | B3 Remove | B4 Missing-required |
|---|---|---|---|---|---|
| 01 Workflow Engine | Production-grade | Full spine wired+tested | Paced dispatch = jitter only (non-cap) | **Dead registry seam** (register_action_executor, supported_action_types, SUPPORTED_TRIGGER_TYPES) | none |
| 02 Builder UI | Complete | Canvas/config/validation/merge-fields all wired | ETag/optimistic concurrency on live edit | none | ETag (low-freq) |
| 03 Voice | Functionally complete | Outcome loop + P9 claim + XC-1b real | none functional | none (spoken-optout detection correctly dormant) | none |
| 04 SMS | Production-grade | Send/idempotency/inbound/STOP/metering | none (residuals cleanly absent) | none | none |
| 05 Email | Launch-compliant **w/ 1 real defect** | Send/unsub/consent/metering | **Bounce/complaint webhook suppresses nothing in prod** | none | webhook fix |
| 06 Campaigns | Complete (agreed scope) | 4 templates wired; dead branches now reachable | none | none (Sales-Qual genuinely absent) | none |
| 07 AI Callback | Complete | End-to-end reachable; loop-guard real | staff-resolve-during-ETA edge | none (tables genuinely absent) | none |
| 08 Campaign UI | Complete (operator scope) | Every action wired to real backend | Per-campaign stat fallback; 200-cap; no component tests | none | **U-2b staff DNC admin UI** |
| 09 Data Layer | Built code complete (~80%) | Projection/ledger/reschedule/freshness wired | none (D-5/D-6 deferred) | none | none in code |
| 10 Provisioning | Complete (agreed scope) | Real AES-256-GCM; resolver; audit | none | stale doc comment (validation_service.py:13-14) | none |
| 11 Usage & Cost | Shipped **w/ attribution gaps** | Metering/rollup/reporting/group/schedule | **SMS + voice invisible in /by-campaign** | none (no usage_budgets cruft) | none |
| 12 Compliance | Complete (scope) | Gate unbypassable; basis hard-block | Group-DNC creation; commercial capture (fail-safe) | none | none blocking |

---

## 3. Bucket 3 — Implemented but unnecessary / should be removed

**This is the bucket the v2 report never hunted. Result: it's small — the codebase is clean.**

**Genuinely removable code (1 item):**
- **Plan 01 — dead action/trigger registry seam.** `register_action_executor()`, `supported_action_types()`, and `SUPPORTED_TRIGGER_TYPES` in `action_registry.py:43-60` are defined but **never called anywhere** in src/ or tests/ — registration is done by the module-level `_ACTION_EXECUTORS` dict literal. Confidence: HIGH. Safe to delete. (`_dispatch_send_stub`, step_dispatcher.py:335-347, becomes dead with it — currently unreachable for all 3 real send node types; keep as a defensive guard OR remove together.)

**Stale comments / doc drift (cleanup, zero functional impact):**
- Plan 10 — `validation_service.py:13-14` comment says readiness "blocks publishing"; it's warning-only. Correct the comment.
- Plan 10 — migration header label `20260703_institution_provisioning` vs actual `revision="20260703_provisioning"`. Cosmetic.
- Plan 11 — `recompute_usage_rollup.py:3` docstring says "5-minute"; infra wires 15-min (intentional). Stale docstring.
- Plan 11 — satisfied `TODO(Plan 03)` comment at `usage_metering_service.py:44-48`. Remove.
- Plan 07 — v2 report cites express-VOICE-consent at `automation_workflow.py:681-692`; actual is `810-832` (doc, not code).

**Confirmed NOT cruft (adversarially checked, genuinely absent — nothing to remove):** Sales-Qualification campaign (Plan 06), `callback_automation_settings`/`callback_workflow_links` (Plan 07), `usage_budgets` (Plan 11), `email_sending_profiles`/`workflow_email_attempts`/`workflow_sms_attempts` (Plans 04/05), `recall_eligibility_working_set` (Plan 09), named `ConsentService`/`SuppressionService` (Plan 12). All were *never built*, not half-built — the "not-required, so not built" claims hold.

---

## 4. Bucket 2 — Implemented but incomplete (prioritized fix-list)

**P1 — fix before relying on the feature:**
1. **Plan 05 — Resend bounce/complaint webhook suppresses nothing in production.** Two compounding causes: the executor sends `tags` as a **list** of `{name,value}` (`email_node_executor.py:130-132`) but the webhook reads it as a **dict** (`email_compliance.py:100`); and Resend doesn't echo custom tags on bounced/complained events anyway → `institution_id` resolves empty → every recipient hits the "missing scope" skip. Tests are green against a payload the system never emits. **The one-click unsubscribe path (the primary opt-out) works** — this only affects automated bounce/complaint suppression. Fix: resolve institution by recipient email→contact/consent, or read tags as a list. (v2 report called this "NEEDS-STAGING-VERIFY" — it's a real defect, not just unverified.)
2. **Plan 11 — SMS and voice are invisible in per-campaign spend (`/by-campaign`).** SMS ingestion leaves `workflow_run_id`/`workflow_id` NULL (`twilio_webhooks.py:280-291`); voice sets `workflow_run_id` but not `workflow_id` (retell webhook). `/by-campaign` groups by `workflow_id`, so only **email** shows per-campaign. Usage *totals* (`/summary`) are exact; only campaign attribution is affected. (v2 report overstates voice attribution.)

**P2 — real but low-frequency / polish:**
- Plan 02 — no ETag/optimistic concurrency on `PATCH .../workflows/{id}`; two admins editing one live campaign → silent last-write-wins.
- Plan 08 — per-campaign secondary stat cards silently fall back to institution-wide totals when a campaign rollup row is empty (misleading); `getUsageByCampaign(…,200)` client-side find → 201st campaign shows $0; no component/render tests for the two pages.
- Plan 12 — group-scope DNC *creation* not exposed (gate honors it; route caps at location/institution); outbound STOP footer is English-only (inbound FR recognition works).
- Plan 07 — staff-resolve-during-ETA-delay not re-checked (narrow; only with a future preferred time).
- Plan 01 — paced/budget-aware dispatch is jitter-only (non-cap smoothing; deferred by product no-caps decision).

---

## 5. Bucket 4 — Missing but genuinely required

**The verification found essentially no structurally-missing functionality.** The one item worth promoting:

- **Plan 08 — U-2b staff DNC admin UI.** The backend (`POST/DELETE/GET /api/institution/do-not-contact`, Plan 12) is real and audited, but there is **no front-end surface**. For an operator-driven pilot, staff have no UI to record a patient who opts out via front desk / phone. The v2 register already flags this **P1**. Recommend building it before pilot. (Not a code gap in the send path — a missing operator affordance on top of a working backend.)

Everything else classified "missing" by the v2 report was re-challenged and confirmed genuinely **deferred / not-required / other-lane** (external DNS for Plan 05, live staging for Plan 09, HTML email, per-clinic Retell isolation, CSV import, revenue analytics), **not** rationalized omissions.

---

## 6. Report-accuracy corrections (feed back into v2)

The v2 report is broadly honest, but this pass found these inaccuracies:
- **Understated:** Plan 05 bounce/complaint webhook ("needs-staging-verify" → actually a prod defect).
- **Overstated:** Plan 11 voice per-campaign attribution (claimed; `workflow_id` is NULL); Plan 12 "bilingual EN/FR STOP" (true inbound; outbound footer English-only).
- **Stale line citations** (code shifted): Plan 01 worker.py/hold-branch ranges; Plan 07 consent block (681-692 → 810-832).
- **Undercounts:** FE test count 130 → 137 (Plans 02/08); email tests "12" → 23; callback "11" → 12.
- **Could not execute** FE `tsc`/vitest (root-owned `node_modules`, EACCES) — FE "tsc clean / tests pass" remains plausible-but-unverified-by-execution.

---

## 7. Test & environment notes

- Backend unit suites for the verified plans were spot-run green (voice 42, callback 12, provisioning/creds/readiness ~35, email 23, compliance 40, Plan-09 33). Full-suite baseline (~1400 unit + 12 real-Postgres integration) trusted per Standard depth.
- **Env gotchas surfaced (relevant to QA setup):** tests must run as `APP_ENV=test` with the venv binary (`.venv/bin/pytest`) — bare `python`/`pytest` fail at prod-config import; some suites need `WEBAUTHN_RP_ID=localhost`. FE tests can't run until `node_modules` ownership/install is fixed. Two pre-existing unrelated failures earlier in the branch (respx missing, Redis down) are environmental, not code.

---

## 8. Recommendation

1. **Proceed to QA.** The implementation is verified complete for an operator-driven pilot; no structural functionality is missing.
2. **Fix before/early in QA:** Plan 05 bounce/complaint webhook (P1 correctness), Plan 11 SMS+voice `workflow_id` tagging (P1 reporting), Plan 08 U-2b DNC admin UI (operator affordance).
3. **Small cleanup PR:** delete the Plan 01 dead registry seam; fix the stale comments/doc drift in §3.
4. **Feed §6 corrections back into the v2 report** so status docs match code.
5. Deferred Plan 05 (domain/DNS) remains a QA/ops ticket, pending CTO sign-off Monday. **Plan 09 (NexHealth staging validation) is now COMPLETE** — the full real-appointment webhook round-trip passed live against the sandbox on 2026-07-15 (book → `appointment_insertion`, cancel → `appointment_updated`, both received/verified/projected end-to-end via a cloudflared tunnel; see `../qa-plan/plan-09-staging-results.md`, Flow 5). Plan 09 = 100%.

**Bottom line for the CTO:** we built the right things, we built them mostly correctly, and we built very little we shouldn't have. Three fixable items and one small cleanup stand between here and a clean pilot.
