# Continuation Prompt — Outbound Engagement Engine · Plan 06 C-1 + C-2 (Confirmation confirmed-branch + PMS confirmation write-back)

> Paste this whole file as the first message in a fresh Claude Code session to resume with full context.
> It is self-contained but points to the authoritative docs to read before acting. **Do not assume anything
> below is still true in code — verify it (see "Verify, don't assume").**

---

## 0. Who you are / how to work (READ FIRST)

You are continuing a large, multi-phase build on the **ScaleNexus** codebase. Follow the repo's own rules:

- **`CLAUDE.md` + `.claude/CLAUDE.md`** are binding. Key rules: **graphify-first** (run `~/.local/bin/graphify
  query "<q>"` / `explain` / `path` before Grep/Read for code questions; the MCP `graphify` server is also
  available), **Planning-with-files** for any 3+ step / multi-file / architectural work, and **model
  right-sizing**. After code changes run `~/.local/bin/graphify update .`.
- **Planning-with-files is MANDATORY** here. Every workstream lives in `docs/new_work/sessions/<kebab-title>/`
  with exactly `task_plan.md`, `findings.md`, `progress.md`. Create a NEW session folder for this workstream
  (suggested: `docs/new_work/sessions/plan-06-confirmation-and-writeback/`); resume an existing one by reading
  all three files first.
- **Product-owner constraints (do not violate):**
  - **NO caps or limits** on clinics/locations, and no tenant-based caps (frequency caps, spend/budget caps,
    blast-radius gates, per-location concurrency caps are **dropped, not deferred**). Non-cap vendor-throughput
    *smoothing* and per-clinic *isolation* are allowed.
  - **Sales Qualification (register C-3) is DEFERRED — do NOT build it in this pass.** There is no lead-intake
    pipeline, so the campaign cannot be enrolled meaningfully; the 4th template slot intentionally stays the
    non-plan **Reactivation** template. Note the deferral in the register/report; do not remove Reactivation.
- **Git:** work on branch **`ali/phase-2`**. Commit only when asked. Commits are **author `Ali2047`
  `<alikkc4024@gmail.com>`, with NO AI attribution** (no `Co-Authored-By`, no "Generated with"). Verify the
  trailer after committing (`git log -1 --format='%an <%ae>'`). This is a **shared branch** (another dev,
  Hammad, also pushes) — before pulling, fetch and check for divergence/conflicts; **merge, never rebase**
  (no history rewrite / force-push); after any merge, confirm `alembic heads` shows exactly ONE head.
- **Auto-approve** was authorized in prior sessions for implementation; confirm with the user if unsure.
- **Do NOT guess missing behavior.** If a requirement/behavior can't be confirmed from code or docs, document
  the ambiguity, explain why it blocks, and do not implement it (see §6, §7).

**Test commands** (Windows / Git-Bash; env vars required):
```bash
JWT_SECRET=test-secret-0123456789abcdef ENCRYPTION_KEY=$(python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())") python -m pytest tests/unit -q
# Real-Postgres integration (needs Docker Desktop running):
TESTCONTAINERS_RYUK_DISABLED=true JWT_SECRET=... ENCRYPTION_KEY=... python -m pytest tests/integration/test_automation_engine_integration.py -q
```
Baseline as of this handoff: **1400 unit + 12 integration pass, 0 failures.** Alembic single head:
**`20260706_usage_cost_rollups`** (reverify with `~/.local/bin/alembic heads`). `alembic heads` must always show
ONE head. **C-1/C-2 likely need NO migration** (the confirmation signal is written into the existing
`automation_workflow_runs.trigger_metadata` JSON column; the write-back is an adapter call). If you do add a
migration, it must be **idempotent** (`ADD COLUMN IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS` / `DROP … IF
EXISTS`), chained off the current head, single-head-preserving, and (for new tables) enable RLS + grant
`nexhealth_app` (mirror `20260702_auto_workflow_core.py` / `20260708_voice_data_model.py`).

---

## 1. The product & the work (session context)

**Platform:** an inbound AI voice agent for dental clinics (ScaleNexus). Retell answers the phone; the backend
gives the agent PMS function-calls via **NexHealth**; staff use a web dashboard. Multi-tenant:
InstitutionGroup → Institution → **Location** (the execution context), isolated by Postgres RLS.

**This scope (`docs/new_work/`): the Outbound Engagement Engine** — makes the platform *proactive*: a
multi-tenant, timezone-aware **workflow engine** + **visual builder** that sends **voice / SMS / email**,
gated by **compliance before every dispatch**. Delivered across 12 implementation plans (01–12).

**This task: Plan 06 (Four Live Campaigns), items C-1 + C-2** — make the **Appointment Confirmation** campaign
actually functional end-to-end. Today it is *send-only*: it sends the confirmation SMS but can never register a
confirmation, so it always exits `no_response`. This is the coupled correctness pair:
- **C-1** — capture the patient's confirmation and drive the confirm branch.
- **C-2** — write the confirmed status back to NexHealth (capability-gated).

It intentionally does **not** build Sales Qualification (C-3, deferred — no lead intake), DB-backed versioned
templates (C-4), or normalized outcome mapping / guided config (C-5). See §4 "OUT of scope."

---

## 2. Documents to read before acting — and how they relate

Read in this order:

1. **`docs/new_work/Outbound_Engagement_Engine_Scope.md`** — the product scope (the "what/why"). Key sections
   for this task: **the four-live-campaigns row** (Confirmation, Reminder, Overdue Recall, Sales Qualification)
   and **§10** (Sales Qualification / lead-intake deferral — why C-3 stays out).
2. **`docs/new_work/Implementation Plans/06-four-live-campaigns.md`** — the Plan 06 spec: template model,
   per-campaign config, **outcome mapping**, PMS **revalidation** before appointment sends, **PMS confirmation
   write-back** ("where NexHealth/PMS support permits"), and the Confirmation default flow
   (revalidate → send → wait for response → **if confirmed, write status to PMS and exit** → else retry/branch).
3. **`docs/new_work/sessions/verification-phase2-v2/report.md`** — the **authoritative status document**
   (re-verified 2026-07-05): per-plan dashboard + per-plan sections + §5 cross-cutting. See **§Plan 06** for the
   current built/missing/bugs list — including the "**confirmed branch is dead code**" bug and "**PMS confirmation
   write-back does not exist**."
4. **`docs/new_work/sessions/outbound-followups-and-gaps.md`** — the **register of remaining work** with stable
   IDs. See the **Plan 06** section: **C-1 (confirmed-branch dead)**, **C-2 (PMS write-back)**, C-3 (Sales
   Qualification — DEFERRED), C-4 (DB-backed templates — deferred), C-5 (outcome mapping/config — deferred).
5. **`docs/new_work/sessions/verification-phase2-v2/plan-06-findings.md`** — deeper Plan 06 audit (dated
   2026-07-03). **⚠️ Partly STALE:** it calls the recall scanner a "STUB" and PMS revalidation "MISSING" — both
   are now BUILT. Treat it as background; **the code + the 2026-07-05 report win.**

**How they relate:** Scope (why) → Plan 06 (what to build) → report (current status/evidence) → register
(remaining work with IDs). When docs disagree with code, **the code wins — reverify.**

Prior session folders (all complete; read for pattern/context, do not modify): `outbound-06-campaign-templates`,
`outbound-01-02-finalize`, `outbound-03-voice-implementation`, `outbound-03-voice-ui-and-closeout`,
`outbound-07-followups-closeout`, `outbound-safety-and-compliance`, `outbound-xc1-send-idempotency`.

---

## 3. Current status (branch `ali/phase-2` @ `71a74e5`, in sync with origin)

Per-plan (from the re-verified report — reverify before relying):
- **01 Workflow Engine ✅ 100%**, **02 Visual Builder ✅ 100%** (verified vs real Postgres; 12 integration tests).
- **03 Outbound Voice 🟢 ~89%**, **04 SMS ~78%** (send-time idempotent), **05 Email ~38%**,
  **06 Four Live Campaigns ~50–55% ← THIS TASK**, **07 AI Callback ~63%**, **08 UI ~22%**,
  **09 Data layer ~40%**, **10 Provisioning ~25%**, **11 Usage/Cost ~65%**, **12 Compliance ~72%**.

**Plan 06 — what's verified BUILT (current tree):**
- In-code **template registry** with 4 templates (`src/app/services/automation/campaign_templates.py:145-184`):
  `appointment-reminder-24h`, `appointment-confirmation-48h`, `recall-sms-6month`,
  `reactivation-sms-email-18month`. (Sales Qualification absent — the 4th slot is Reactivation.)
- **Reminder** — fully live end-to-end (appointment-offset trigger wired: webhook → `trigger_appointment_workflows`
  → `enroll_and_start_workflow_run` with ETA from `offset_hours`).
- **Recall** — now REAL (live `list_patient_recalls` → due-date filter → idempotent paced enrollment).
- **`PmsLiveRevalidationService`** live-backed and injected into every dispatch path (catches cancelled/rescheduled).
- **Compliance gate** before every send; template list/get/instantiate API (`api/routes/automation_templates.py`);
  template schema tests pass.
- Inbound SMS webhook exists (`api/routes/twilio_webhooks.py`) — but see §4, it only handles STOP/START/HELP.

**Plan 06 — what's MISSING / broken (register IDs):**
- **C-1 (P1)** Confirmation "confirmed" branch is **dead code** — nothing writes `appointment_status` into run
  state, so the confirm condition is always false → the run always exits `no_response`. (Mirrored in the
  Reactivation `appointment_booked` branch.) **← THIS TASK.**
- **C-2 (P1)** **PMS confirmation write-back** — no confirm/update-status adapter method; Confirmation can't
  push status back to NexHealth. **← THIS TASK (capability-gated).**
- **C-3 (P1) — DEFERRED this pass:** Sales Qualification campaign (no lead intake). Do NOT build.
- **C-4 (P2) — deferred:** DB-backed versioned `workflow_templates` / `_versions`.
- **C-5 (P2) — deferred:** normalized outcome mapping + channel-order/fallback/attempt-ceiling config.

---

## 4. The task — Plan 06 C-1 + C-2 (pick up HERE)

Two coupled correctness bugs on the **Appointment Confirmation** campaign (evidence verified against the current
tree 2026-07-05 — **reverify each `file:line`**):

### C-1 — Confirmation "confirmed" branch is dead code (P1, correctness / patient-facing)
**Symptom:** the Confirmation campaign sends its SMS, waits, then evaluates a ConditionNode on
`appointment_status == "confirmed"` — but **nothing ever writes `appointment_status` into the run context**, so
the condition is always false and every run exits `no_response`. The clinic never learns the patient confirmed.

Chain of causes (verify):
- The template condition keys on `appointment_status` (`campaign_templates.py:~69`); the Reactivation template
  has the same class of dead branch on `appointment_booked` (`campaign_templates.py:~119`).
- The ConditionNode evaluates rule fields against the run context
  (`services/automation/step_dispatcher.py` → `_evaluate_rule` → `context.get(rule.field)`); nothing populates
  `appointment_status`.
- Inbound SMS replies **are received** at `api/routes/twilio_webhooks.py` `inbound_sms` (`:74-149`) but only
  **STOP / START / HELP** keywords are acted on — **any other reply (e.g. "YES", "C", "CONFIRM", "1") is logged
  and ignored** (`:141-149`), with **no linkage to a workflow run**. So a patient's "YES" does nothing.
- The templates contain **no `SendVoiceNode`** (module docstring `:4-7`), so the Plan-03 voice outcome loop does
  NOT rescue this — the branch keys on `appointment_status`, not `call_outcome`.

**Goal:** when a patient confirms, the confirm branch fires. The natural, patient-driven mechanism is an
**inbound-SMS reply → run linkage**: detect a confirmation reply from a patient who has an active confirmation
run at that location, write `appointment_status = "confirmed"` (plus the raw reply for audit) into that run's
`trigger_metadata`, and **resume the parked run early** so the ConditionNode takes the confirmed branch. **Mirror
the existing `resume_voice_outcome` / `_resume_voice_outcome_async` pattern** (`tasks/automation_workflow.py:735-856`):
find the parked step/run, cancel its safety/wait timer, write the outcome into `run.trigger_metadata`, and call
`dispatcher.resume_after_timer(...)`. Keep it **at-most-once** (the `run.status == WAITING` guard makes a
reply/timer race safe). If no reply arrives before the wait timer fires, the run correctly falls through to
`no_response` — that behavior stays.

An alternative/complementary mechanism is **reading a confirmation status from NexHealth at revalidation time**
(if the PMS exposes a "confirmed" flag) — but that depends on the NexHealth API and is an ambiguity to resolve
(§6), not to assume.

### C-2 — PMS confirmation write-back (P1, correctness — capability-gated)
**Symptom:** the adapter has `book_appointment` (`pms/nexhealth/adapter.py:~409`), `cancel_appointment` (`:~442`),
`reschedule_appointment` (`:~458`), `update_appointment_type` (`:~535`) — but **no confirm / update-status
method**, and no campaign path writes a confirmation back to NexHealth. The plan's "if confirmed, write
confirmation status to PMS where supported and exit" is unimplemented.

**Goal:** on the confirmed branch, write the confirmation back to NexHealth **where the API supports it**. This is
**capability-gated**: first resolve whether the NexHealth API exposes appointment confirmation-status write-back
(§6 — factual ambiguity; verify from the adapter/NexHealth docs, web-search if needed). If supported, add a
capability-checked adapter method (e.g. `confirm_appointment` / `update_appointment_status`) and call it on the
confirm branch, fail-open + audited on error (never break the patient-facing send). If NOT supported, **do not
fake it** — record the confirmation internally only, mark write-back as unsupported for that PMS, and document it.

### Explicitly OUT of scope for this slice
- **C-3 Sales Qualification — DEFERRED** (no lead intake; keep the Reactivation template; do not build). Just
  note the deferral in the register/report.
- **C-4** DB-backed versioned `workflow_templates` / `_versions` (templates stay in-code dataclasses).
- **C-5** the full normalized outcome-mapping vocabulary + guided per-campaign config (channel-order, fallback,
  attempt ceilings, retry limits, staff-handoff settings). Only add the **minimal** outcome plumbing C-1 needs
  (e.g. an `appointment_confirmed` / `confirmed` exit that already exists in the template) — do not build the
  cross-campaign outcome map.
- Decide in `findings.md` whether to **also** fix the Reactivation `appointment_booked` dead branch in the same
  pass (same class of bug) or leave it noted — Reactivation is not one of the four scoped launch campaigns, so
  Confirmation is the priority; document the choice.

**Regression risks to protect:** the inbound-SMS **STOP/START/HELP opt-out** handling (do NOT break compliance
suppression/release — a confirmation-reply handler must run *after* and *never swallow* opt-out keywords); the
shared wait/hold-resume path (WaitNode, quiet-hours hold, parked-voice); the cancellation cascade (runs + timers);
send-time idempotency (`already_sent`); **do not double-contact**; keep revalidation fail-open for genuine PMS
outages; keep the Twilio webhook signature verification intact.

---

## 5. Required investigation workflow (do this before writing code)

1. **Understand the architecture first.** Trace the complete Confirmation flow against CURRENT code before
   changing it: template definition → enrollment → send → wait → ConditionNode. Use graphify to orient, then read
   the specific files. Key seams for C-1/C-2:
   `services/automation/campaign_templates.py` (the confirmation template + its `appointment_status` condition),
   `services/automation/step_dispatcher.py` (`_evaluate_rule` / ConditionNode; how run `context` is read; where
   sends and revalidation happen),
   `api/routes/twilio_webhooks.py` (`inbound_sms` — STOP/START/HELP classify; where a confirmation reply would
   hook in; signature verify; location resolution by `To` number),
   `tasks/automation_workflow.py` (`_resume_voice_outcome_async` as the **resume pattern** to mirror; enrollment;
   `trigger_metadata` shape),
   `services/automation/revalidation.py` (`PmsLiveRevalidationService` — could also read a PMS confirmation
   status), and `pms/nexhealth/adapter.py` (existing write methods; whether a confirm/update-status endpoint fits).
2. **Confirm how a patient confirms in the real product.** Is it an SMS reply ("YES"/"C"), a PMS-side status the
   clinic sets, or both? Resolve the mechanism (§6) before coding — do not assume.
3. **Cross-reference docs with implementation.** Confirm each report/register claim in code (`file:line`) before
   relying on it. Remember `plan-06-findings.md` is partly stale.
4. **Verify, don't assume.** Prior findings were true when written; re-verify against the current tree (files,
   signatures, migrations). If a finding named a file/function/flag, confirm it still exists.
5. **Identify new findings, gaps, dependencies, regression risks.** Note anything not already tracked; add it to
   the register/session findings.
6. **Determine the safe order** and the **affected files + regression risks** before editing.

---

## 6. Ambiguities to resolve BEFORE implementing (do not guess — see §7)

- **Confirmation mechanism (product):** how does a patient confirm — inbound SMS reply, a PMS-side status the
  clinic marks, or both? Recommended primary: **inbound-SMS reply → run linkage + early resume** (patient-driven,
  reuses the `resume_voice_outcome` pattern). Route the decision to the user; do not assume the PMS-poll path
  exists without evidence.
- **Confirmation reply keywords (product):** which reply tokens mean "confirmed" (e.g. `YES` / `Y` / `C` /
  `CONFIRM` / `1`)? Localization? Ambiguity between "confirm" and "reschedule/cancel" replies? These are
  product/safety decisions — propose a conservative default set and route the exact list to the user.
- **NexHealth confirmation write-back support (factual):** does the NexHealth API expose appointment
  confirmation-status write-back (a PATCH/status endpoint or a "confirmed" field)? Verify from the adapter/client
  and NexHealth docs; web-search public NexHealth API docs if unclear. If unsupported, C-2 degrades to
  internal-only — document it; do not fabricate an endpoint.
- **Early-resume vs wait-timer (safety):** a reply arriving before the confirmation wait timer should resume the
  run early and take the confirmed branch; a reply after the run already exited must NOT re-open a terminal run
  (at-most-once). Confirm the `run.status == WAITING` guard covers the race.
- **No-caps / compliance:** the reply handler must not weaken STOP/START/HELP opt-out handling and must not
  introduce any per-clinic cap.

## 7. Mandatory rules for handling ambiguity

- If a behavior/requirement/field can't be confirmed from code or docs → **do NOT implement it.** Document the
  ambiguity (what, why it blocks, what evidence/decision is needed) in the session `findings.md` / an
  `ambiguity-review.md` and the register, and tell the user.
- Web-search public vendor docs (NexHealth) / regulatory sources to resolve *factual* ambiguities; route
  *product/safety* decisions (confirm mechanism, reply keywords) to the user.
- Compliance/patient-safety logic (consent, opt-out, double-contact, at-most-once) must be correct, not
  approximated. Bias to **at-most-once** (never double-contact) and never weaken the STOP/START/HELP path.

## 8. Phased workflow for the work item

1. **Plan** — create the session folder; write `task_plan.md` (phases, deps, safe order, affected files,
   regression risks) BEFORE coding. Record decisions.
2. **Investigate** — §5 above; write grounded `findings.md` (2-action rule: after ~2 reads, append findings).
3. **Verify assumptions** — reverify prior findings against code; confirm the Alembic head; run the relevant test
   subset to establish a green baseline.
4. **Implement in verified increments** — smallest coherent slice first. Suggested order: **C-1** (inbound-reply →
   run linkage + early resume; the confirm branch fires) is the higher-value, more self-contained fix; **C-2**
   (capability-gated PMS write-back on the confirmed branch) layers on top. Keep changes additive; do NOT break the
   STOP/START/HELP opt-out path, the shared wait/hold-resume path, or the cancellation cascade. Migration is likely
   unnecessary (write into existing `trigger_metadata` JSON); if added, keep it idempotent, chained off the current
   head, single-head-preserving, RLS + grants for any new table.
5. **Test each increment** — unit + extend the real-Postgres integration suite
   (`tests/integration/test_automation_engine_integration.py`) for DB-backed mechanics (a confirmation reply
   flips the run to the confirmed branch and resumes; a non-confirm reply / no reply still falls through to
   `no_response`; STOP still opts out; write-back is called only when supported). Full `tests/unit` green before
   commit. Prove any regressions are pre-existing (baseline worktree) if they surface.
6. **Notate** — update `progress.md`, the report (Plan 06 status + the C-1 dead-branch bug once fixed), and the
   register (C-1/C-2 status; C-3 explicitly deferred). Keep them consistent.
7. **Commit** (when asked) — author Ali2047, no AI attribution; then `~/.local/bin/graphify update .`; push
   `ali/phase-2` only when asked (fetch/merge first if the shared branch moved).

---

## 9. Suggested first message to the user in the new session

"Resuming the Outbound Engagement Engine on **Plan 06 (Four Live Campaigns)**. I've read the scope, the Plan 06
doc, the re-verified report (Plan 06 ~50–55%), and the register (C-1..C-5). The task is **C-1 make the
Appointment Confirmation confirmed-branch functional** (today it's send-only — inbound replies other than
STOP/START/HELP are ignored, so `appointment_status` is never set and every run exits `no_response`) and **C-2
PMS confirmation write-back** (no confirm/update-status adapter method exists), as a bounded slice.
**Sales Qualification (C-3) is deferred** (no lead intake) and C-4/C-5 are out of scope. Before implementing I'll
verify the confirmation → inbound-SMS → run-context flow against the code and resolve a few decisions with you:
(1) the confirm mechanism (inbound-SMS reply → resume, mirroring `resume_voice_outcome`), (2) which reply keywords
mean "confirmed," and (3) whether the NexHealth API supports confirmation write-back (else C-2 degrades to
internal-only). Starting with findings — shall I proceed?"
