# Platform Completion — Task Audit

**Date:** 15 October 2026
**Audited against:** `feat/recall-campaign` (= `origin/staging`, HEAD `ca408e7d`)
**Source list:** `DEV-Platform-Completion-Tasks.md` (16 items: Tasks 1–15, with 8 split into 8A/8B)
**Purpose:** Reconcile the client's task list with the actual state of the code, so we
build only what is genuinely missing.

## Legend

| Mark | Meaning |
|---|---|
| ✅ | Done and reachable from `origin/staging` / `origin/production` |
| 🟡 | Done in code but **not deployed** (sits on a local branch) |
| 🟠 | Partly done — specific gaps remain |
| 🔴 | Not started |
| 📋 | Not a coding job (verification, doc, sign-off, or decision) |

## Update — 2026-10-15 (branch `fix/platform-completion`)

The branch was rebased onto `origin/staging` (`ca408e7d`) and now carries:

- Task 7 + Task 8A S1–S3 — the original six fixes (rebased, tests pass).
- Task 2 — reminder offered in the gallery again, with its tests updated.
- Task 8A S4 — M-6 (NexHealth webhook replay window) fixed; the rest recorded
  in `docs/HIPAA_CODE_AUDIT_REMEDIATION.md`.
- Task 10 — backend/frontend coverage tooling and a GitHub Actions CI job.
- Task 14 — `docs/STAGING_DATA_POLICY.md`.
- Task 5 — verified as already implemented and tested (no code needed).

Deferred: Task 13 (blocked on the GoTracker cloud heartbeat contract), Task 3's
cloud endpoint and Task 6 (both in the RDP repo), Tasks 1/4 (production proofs),
Tasks 8B/9 (docs), Tasks 11/12 (client accounts / product decision).

**Known pre-existing failures:** `origin/staging` already has 95 failing unit
tests (`test_enquiry_intake_route` 31, `test_enquiries_api` 23,
`test_test_suite` 22, `test_enquiry_intake_admin` 10, and a few others). These
predate this branch — the rebase introduced no new failures — but the new CI
job will be red until they are triaged.

## The one branch that matters

`fix/platform-completion` — **local only, not yet pushed.** Cut from `staging` on
6 Oct, rebased onto `origin/staging` (`ca408e7d`) on 15 Oct. It now carries the
original six Task 7 / Security S1–S3 commits (rebased) plus the Task 2, S4-M6,
Task 10 and Task 14 work described in the update above.

Original six (hashes below are pre-rebase; see `git log` for the rebased ids):

| Commit | Item |
|---|---|
| `de02f510` | Task 7 — descriptor key on appointment-type create |
| `1f147471` | Task 7 — hidden providers out of the voice-agent slot search |
| `0a10d73f` | Task 7 — `nh-` prefixed appointment-type ids in completed-visit sweep |
| `1f9b662e` | Task 8A · S1 — scope NexHealth location listing to the caller's clinic |
| `b4c949a5` | Task 8A · S3 — withhold procedures + insurance member ids from the voice model |
| `c49908ba` | Task 8A · S2 — appointment ownership check on voice cancel/confirm/reschedule |

## Task-by-task audit

| # | Task | Necessary? | Actual state | Evidence / what's missing |
|---|---|---|---|---|
| 1 | Confirmation campaign — live proof on both clinics | Yes (proof only) | 📋 Code ✅, proof 🔴 | Template `surgery-pre-appointment-confirmation`; NexHealth `confirm_appointment` (`pms/nexhealth/adapter.py:1376`); GoTracker `confirm_appointment` (`pms/gotracker/adapter.py:691`); `ComplianceGate` + `quiet_hours_service.py` present. Missing: production run log, 10 confirmations/clinic, quiet-hours + consent proof. |
| 2 | Reminder as its own campaign | **Yes (P1)** | 🟡 Fixed on branch | Was excluded from the gallery in `list_templates()`. Now offered again; tests updated. The trigger already restricts sends to still-active statuses, so cancelled/rescheduled appointments are not reminded. Remaining: QA the Confirmation+Reminder double-message rule. |
| 3 | Overdue Recall — fix and run for both clinics | **Yes (P1)** | 🟠 | Tick now passes `workflow`/`version_id` and reads the real counter (`automation_workflow.py:3560–3600`); pull is paginated (`patient_recalls`); `tick-workflow-schedules` is on beat (60 s). **Missing:** GoTracker cloud endpoint `GET /api/appointments/history-status` still called (`pms/gotracker/adapter.py:642`) but not served → Olive Tree skipped; recall-type intervals need a check against NexHealth types. |
| 4 | Sales Qualification — end-to-end proof | Yes (proof only) | 📋 Code ✅, proof 🔴 | `enquiry_intake_service.py`; `form_integrations.py` / `form_webhooks.py` (Meta/Typeform); `LeadStatus` incl. `QUALIFIED`/`NOT_QUALIFIED`/`BOOKED` (`models/contact.py:31`). Missing: production recording form → qualified → booked. |
| 5 | Cancelled / confirmed triggers | Yes | ✅ (verify) | `TRIGGER_EVENT_KEYS` maps `appointment.cancelled/confirmed/...` (`trigger_lookup.py`); NexHealth webhook fires `trigger_appointment_workflows` after cancel (`nexhealth_webhooks.py:1279,1319`); GoTracker webhook does the same (`gotracker_webhooks.py:608,693`). Missing: the "native trigger flag" match-check the task asks for, plus recordings. |
| 6 | GoTracker write test + report | **Yes (P1, gate)** | 🔴 | GoTracker repos are **not on this machine**; nothing in this repo. Needs seeded Tracker DB, every write type, failure cases, written report, signed-off before any live write. Cross-repo. |
| 7 | QA fixes on Reminder / Recall | **Yes (P1)** | 🟡 | All three defects are fixed only on `fix/platform-completion`. Verified still broken on staging: completed-visit sweep has no `nh-` prefix match; create sends `appointment_descriptor_ids` vs update's `emr_appt_descriptor_ids` (`adapter.py:1629/1663`); `find_appointment_slots` does not filter hidden providers (`handlers.py:924`). |
| 8A | Security fixes S1–S4 | **Yes (P2)** | 🟠 S1–S3 🟡, S4 🔴 | S1–S3 on `fix/platform-completion`. Verified still broken on staging: `list_locations` unscoped (`adapter.py:1525`); no `retell/caller_identity.py`; patient payload still sends `insurance_coverages` + `procedures` (`handlers.py:238–239,649–650`). S4 = the 29 Aug HIPAA audit items (2 High, 8 Medium) — not in repo, not fixed. |
| 8B | Operations documents | Later | 🔴 | No DR/backup/deploy/runbook/pen-test docs. `docs/PRIVACY_AUDIT_REVIEW.md` exists (the broken link is fixed). Needs DR drill in staging. |
| 9 | Documentation package | Later | 🟠 | `docs/adr/` has 2 ADRs; some onboarding docs exist. Missing: GoTracker cloud API docs, PMS adapter contract, schema map + stored-proc map, queue protocol, clinic admin guides (PDF + in-app). |
| 10 | Test coverage + live integration tests | Quality | 🔴 | ~3,060 backend / 480 frontend tests, but **no coverage tool and no CI**; only NexHealth has live tests (`RUN_LIVE_NEXHEALTH=1`). Missing `pytest-cov`, Vitest coverage, CI job. |
| 11 | Move to the client's accounts | Later (blocked) | 🔴 | `alarmEmail` is still `zulkhaifahmed@gmail.com` in `infra/config/production.json:11` and `staging.json:10`. Blocked until the client creates the AWS account + GitHub org. |
| 12 | Revenue per campaign | Nice-to-have | 🔴 | `CampaignDetail.tsx` has no revenue; only the dashboard ROI estimate exists. Needs an agreed attribution rule first. |
| 13 | GoTracker connection warning for clinic staff | Yes-ish | 🔴 | No connector/heartbeat warning in the dashboard or API. |
| 14 | Staging data policy | Yes (cheap) | 🔴 | No such doc exists. |
| 15 | SMS carrier registration check | Yes (cheap) | 🔴 | No recorded status. |

## Summary

- **16 items:** ✅ 1 fully live (Task 5, pending verification) · 🟡 2 done-but-undeployed (7, and security S1–S3 of 8A) · 🟠 3 partial (2, 3, 8A) · 📋 2 verification-only (1, 4) · 🔴 the rest.
- **Only Task 3 (Recall) is confirmed in production** (promoted 14 Oct, `origin/production` = `9ae1bc34`).
- **Undeployed finished work:** the entire `fix/platform-completion` branch — real, tested security + QA fixes that are not on any remote.

## Recommended order

1. **Ship the branch.** Rebase `fix/platform-completion` onto latest `origin/staging`, run tests, push, PR → staging. (Tasks 7 + 8A S1–S3.)
2. **Real code gaps:** Task 2 (reminder), Task 8A S4, Task 13.
3. **Verify + record (no code):** Tasks 1, 4, 5.
4. **Cross-repo / gated:** Task 3 GoTracker endpoint, Task 6 write test.
5. **Cheap compliance:** Tasks 14, 15, and Task 10 coverage/CI.
6. **Later / blocked:** docs (8B, 9), Task 11 (client accounts), Task 12 (decision).

## Production inbound-agent impact (only on promotion)

Deploying to **staging** does not change production. On **promotion to production**,
these change inbound-agent behaviour and need testing/sign-off first:

- **S2** — the agent refuses cancel/confirm/reschedule unless the caller was verified that call.
- **S3** — the agent stops receiving procedures and insurance member ids.
- **Task 7** — hidden providers disappear from "any provider" slot search.

Confirm staging's Retell agent/number is separate from production's before deploying prompt/config changes.
