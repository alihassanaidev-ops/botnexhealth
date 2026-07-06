# Confirmation Capture — Mechanism Options (DECISION DEFERRED)

> **Status:** open decision, parked for later. This documents the two ways a patient's appointment
> confirmation can be captured and drive the Appointment Confirmation campaign's "confirmed" branch
> (Plan 06, register item **C-1**). No code for either path is written yet. Once you pick, implementation
> follows the session plan at `docs/new_work/sessions/plan-06-confirmation-and-writeback/`.
>
> Scope note: this file is ONLY about *how we learn the patient confirmed* (C-1 capture). The separate
> **C-2** step — writing `confirmed=true` back to NexHealth once the branch fires — is unaffected by this
> choice and is already factually confirmed as supported (`PATCH /appointments/{id}` `{"appt":{"confirmed":true}}`).

---

## Background — why this decision exists

Today the Appointment Confirmation campaign is **send-only**:

1. Sends an SMS 48h out: *"…please confirm your upcoming appointment. Reply YES to confirm…"*
2. Waits 2 hours (`WaitNode`, `duration_seconds=7200`).
3. Evaluates a `ConditionNode`: `appointment_status == "confirmed"`.
4. **Nothing ever sets `appointment_status`**, so the condition is always false → every run exits
   `no_response`. The clinic never learns the patient confirmed.

Files (verified 2026-07-05):
- Template: `src/app/services/automation/campaign_templates.py:46-77`
- Condition eval: `src/app/services/automation/step_dispatcher.py:355-369` (`_evaluate_rule` reads
  `context.get("appointment_status")`; `context` == the run's `trigger_metadata`)
- WaitNode early-resume seam: `step_dispatcher.py:257-333` (`resume_after_timer`)

To fix C-1 we must populate `appointment_status="confirmed"` on the run. There are two credible sources
for that signal, described below. They are **complementary, not mutually exclusive**.

---

## Option A — Inbound SMS reply → run linkage + early resume  (patient-driven, real-time)

### How it works
The patient replies to the confirmation text (e.g. "YES"). We already receive every inbound SMS at the
Twilio webhook `inbound_sms` (`src/app/api/routes/twilio_webhooks.py:74-149`), where STOP/START/HELP are
handled and **every other reply is currently logged and ignored** (`:141-149`). We add confirmation
handling *after* the opt-out branches:

```
Patient texts "YES"
  → inbound_sms verifies Twilio signature, resolves location by the "To" number
  → (STOP/START/HELP handled first — untouched)
  → classify "YES" as a confirmation reply
  → enqueue resume_sms_confirmation(institution, location, from_number, body)   [Celery, keeps webhook fast]
        → hash_phone(From) → Contact  (Contact.phone_hash lookup, contact.py:152-155)
        → find WAITING run(s) for (contact_id, location_id) whose current WaitNode's next node
          is a ConditionNode keying on `appointment_status`   [template-agnostic match]
        → cancel the 2h wait timer (cancel_timers_for_run)
        → write trigger_metadata["appointment_status"] = "confirmed" (+ raw reply for audit)
        → dispatcher.resume_after_timer(...)  → ConditionNode true → exit-confirmed
        → (C-2) write-back confirmed=true to NexHealth
  → return TwiML acknowledgement to the patient
```

This mirrors the **proven voice pattern** `resume_voice_outcome` / `_resume_voice_outcome_async`
(`src/app/tasks/automation_workflow.py:735-856`), which already does exactly this shape for voice-call
outcomes (find parked step, guard `run.status==WAITING`, cancel timer, write outcome into
`trigger_metadata`, `resume_after_timer`).

### Timing
Real-time — the branch fires within seconds of the reply, before the 2h timer. If no reply arrives, the
2h timer fires the same path with no `appointment_status` → falls through to `no_response` (unchanged).

### Pros
- **Real-time** confirmation; clinic sees it immediately.
- **Reuses an existing, tested pattern** (voice resume) — low architectural risk.
- **No added load** on the shared NexHealth API key.
- Naturally patient-driven and matches the SMS's own call-to-action ("Reply YES").
- At-most-once for free via the `run.status==WAITING` guard (reply/timer race is safe).

### Cons / limits
- Only captures confirmations that arrive **as an SMS reply to our Twilio number**. A patient who
  confirms by **phoning the clinic** or the **front desk marking them confirmed in the PMS** is not seen
  by this path (that run still exits `no_response` after 2h).
- Requires careful ordering so it never weakens STOP/START/HELP opt-out (mitigated: handled strictly
  after opt-out; confirm keywords chosen to never overlap opt-out keywords).

### Effort
Moderate. New: a keyword classifier, a `resume_sms_confirmation` Celery task (clone of the voice one), a
few lines in `inbound_sms`, unit + integration tests. **No DB migration** (writes into existing
`trigger_metadata` JSONB).

---

## Option B — Poll NexHealth's `confirmed` flag  (clinic/PMS-driven, safety-net)

### How it works
NexHealth appointments carry a `confirmed` boolean, readable via `GET /appointments/{id}` (the adapter
already fetches this record — `pms/nexhealth/adapter.py:313` `get_appointment`, used by
`PmsLiveRevalidationService`, `services/automation/revalidation.py:104-138`). When a patient confirms by
phone, or the front desk marks them confirmed in the PMS, that field flips to `true`.

We read that flag and, if set, drive the confirm branch. The natural, low-cost hook is **at the moment
the confirmation wait resolves** (i.e. inside/alongside the existing revalidation read that already runs
before dispatch), extended to also check `confirmed`:

```
Confirmation run's 2h wait timer fires (or a resume is triggered)
  → revalidation already reads GET /appointments/{id}  (cancelled? rescheduled?)
  → ALSO read appt["confirmed"]; if true → set appointment_status="confirmed" in context
  → ConditionNode true → exit-confirmed → (C-2) write-back
```

(A more aggressive variant would actively poll every N minutes during the 2h window, but that multiplies
load on the shared NexHealth key and is **not recommended** — see Cons.)

### Timing
Not real-time. In the "check at wait-resolve" form, the confirm is only detected when the 2h timer
resolves the run — so a phone confirmation made at hour 0 isn't acted on until the wait ends. Active
polling would be fresher but costly.

### Pros
- **Catches confirmations that never come as an SMS** (phone calls, front-desk marking) — the gap in
  Option A.
- Reuses the existing revalidation read, so the passive form adds ~no extra API calls.
- PMS is the source of truth the clinic already trusts.

### Cons / limits
- **Depends on NexHealth's sync actually populating `confirmed`** for the clinic's PMS/EHR — needs
  verification per health-record system (NexHealth's write/sync support varies by PMS; confirmed field
  read support should be broad but must be validated with a real tenant).
- **Not real-time** in the low-cost form; **expensive** in the fresh (active-poll) form — active polling
  during the window adds load on the *shared* NexHealth API key across all tenants (pacing/rate-limit
  pressure), with **no caps allowed** per product policy, so smoothing is the only lever.
- Slightly more coupling of the revalidation seam (it currently only *skips* sends; reading a positive
  "confirmed" to *advance* a branch is a new responsibility).
- Ambiguity: if both a "cancelled" and a stale "confirmed" appear, precedence rules must be defined
  (cancelled should win).

### Effort
Low-to-moderate for the passive (wait-resolve) form; higher and riskier for active polling. Requires a
tenant-level verification that `confirmed` is reliably synced.

---

## Interaction of A + B ("Both")

- A confirm from **either** source sets `appointment_status="confirmed"` and drives the same branch; C-2
  write-back runs once regardless.
- A is the **fast path** (real-time, SMS repliers); B is the **safety net** (phone / front-desk
  confirmers), detected when the wait resolves.
- Must be **at-most-once**: if A already confirmed and resumed the run (now terminal), B must not re-open
  it — the `run.status==WAITING` guard covers this.
- Cancelled/rescheduled (existing revalidation outcomes) must take precedence over a stale `confirmed`.

---

## Recommendation (for when we revisit)

Implement **Option A first** (real-time, self-contained, reuses a proven pattern, zero NexHealth load),
then **layer Option B (passive, at wait-resolve)** as a safety net once we've verified with a real tenant
that NexHealth reliably syncs the `confirmed` flag for their PMS. Avoid active polling (load on the shared
key, no caps allowed). This gives real-time coverage for SMS repliers immediately and closes the
phone/front-desk gap without new steady-state API cost.

## Open questions to answer before building B
1. Does NexHealth reliably populate `confirmed` on `GET /appointments/{id}` for our target tenants' PMS?
   (Verify with a live sandbox/tenant — read support should be broad but confirm.)
2. Is "detect at wait-resolve" fresh enough, or is active polling required? (Active polling = shared-key
   load; needs pacing.)
3. Precedence when cancelled + confirmed both appear (cancelled wins).

---

*Owner decision pending. When decided, record it in
`docs/new_work/sessions/plan-06-confirmation-and-writeback/task_plan.md` and the register
`docs/new_work/sessions/outbound-followups-and-gaps.md` (C-1).*

---
---

# Related open decisions (also deferred)

Three smaller product/safety decisions surfaced during the C-1/C-2 investigation. They are parked here
with full context so we can settle them together when we revisit the mechanism choice above. Decisions 1
and 2 are entangled with the mechanism; Decision 3 is an independent pre-existing bug in a different
campaign.

---

## Decision 1 — Which reply tokens count as "confirmed"?

**Applies only if we build Option A (inbound SMS reply).** If we go PMS-poll-only (Option B), there is no
reply to classify and this decision is moot.

### Context
The confirmation SMS asks the patient to *"Reply YES to confirm."* When the reply lands at `inbound_sms`
(`src/app/api/routes/twilio_webhooks.py:74-149`), it is tokenized and matched against keyword sets. The
existing opt-out/help classifier `_classify_intent` (`:57-71`) uses these sets (`:43-48`):

```
STOP_KEYWORDS  = {STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT,
                  ARRET, ARRÊT, DESABONNER, DÉSABONNER, RETIRER, SUPPRIMER}   # incl. French (CASL)
START_KEYWORDS = {START, UNSTOP}
HELP_KEYWORDS  = {HELP, INFO, AIDE}
```

Tokenization is on any non-letter run (`_TOKEN_RE`, `:54`), body uppercased first. A confirmation keyword
set **must not overlap any of the above** (opt-out safety always wins), and confirmation must be
classified **after** STOP/START/HELP so it can never swallow an opt-out.

### Options
| Option | Set | Notes |
|---|---|---|
| **A (recommended)** | `YES, Y, CONFIRM, C, 1` | Covers the template's "Reply YES" + common variants. None overlap opt-out keywords. `C`/`1` catch terse replies. |
| B (safest/narrow) | `YES, Y, CONFIRM` | Drops single-char `C` and numeric `1` to avoid stray-reply false positives (e.g. a patient typing "1" for an unrelated reason). |
| C (bilingual) | `YES, Y, CONFIRM, C, 1, OUI, O, CONFIRMER` | Matches the French opt-out keywords already supported for CASL; use if any target tenants serve French-speaking patients. |

### Safety considerations
- Never overlap STOP/START/HELP (verified: none of the proposed tokens do).
- Ambiguous replies ("YES but move it earlier", "confirm and reschedule") — a conservative set treats a
  bare confirm token as confirm; anything mixed falls through to `no_response` (safe: no false confirm).
- Localization beyond French is out of scope unless a tenant needs it.

### Recommendation
Option A (`YES, Y, CONFIRM, C, 1`), add French (Option C) only if a target tenant serves French patients.

---

## Decision 2 — The "CANCEL to cancel" wording conflict (pre-existing bug)

**Independent of the mechanism choice** — this is wrong today regardless of A/B.

### Context
The confirmation template body (`campaign_templates.py:53-56`) reads:

> *"Hi {{patient_first_name}}, please confirm your upcoming appointment. Reply YES to confirm or **CANCEL
> to cancel**. Reply STOP to opt out."*

But `CANCEL` is a member of `STOP_KEYWORDS` (`twilio_webhooks.py:45`). So a patient who follows the
instruction and replies "CANCEL" is **suppressed from all future SMS** from that location (opt-out +
`SmsSuppression` record) — they are **not** routed to an appointment cancellation. The instruction is
actively misleading and has a compliance side effect (silences the patient), so it should not ship as-is.

### Options
| Option | Action | Trade-off |
|---|---|---|
| **A (recommended)** | Reword the SMS: drop "or CANCEL to cancel", keep "Reply YES to confirm. Reply STOP to opt out." | Minimal, removes the misleading text. Cancel-from-SMS stays unbuilt (patients cancel via phone/clinic as today). |
| B | Leave wording, document the limitation | Zero code now, but keeps shipping a misleading instruction that opts patients out. Not advised. |
| C | Build real cancel-from-SMS | A distinct cancel reply routes to a PMS appointment cancel (adapter `cancel_appointment` already exists, `adapter.py:442`) + a new cancel branch. Larger scope — beyond C-1/C-2; overlaps C-5 outcome mapping. |

### Recommendation
Option A (reword). If cancel-from-SMS is genuinely wanted, schedule it as its own item (it needs a cancel
branch in the template, a cancel keyword set distinct from STOP, and PMS write-back cancel — a real
feature, not a wording fix).

---

## Decision 3 — The Reactivation template's dead branch (same bug class, different campaign)

**Independent of the mechanism choice.**

### Context
The Reactivation template (`campaign_templates.py:96-138`) has the same structural dead branch as
Confirmation: after its SMS + 48h wait it evaluates a `ConditionNode` on
`{field: appointment_booked, op: eq, value: True}` (`:118`) — but **nothing ever sets `appointment_booked`**
on the run, so the condition is always false and every reactivation run falls through to the email
follow-up (its false branch) and exits `email_sent`. The `exit-booked` branch is unreachable.

Unlike Confirmation, Reactivation is **not** one of the four scoped launch campaigns for this work, and
"appointment booked" is a different, harder signal to capture than an SMS "YES" (it implies detecting a
new booking — via NexHealth appointment creation or a booking-intent reply — not a simple keyword).

### Options
| Option | Action | Trade-off |
|---|---|---|
| **A (recommended)** | Leave noted; fix Confirmation only this pass | Keeps the slice bounded to the scoped campaign. Documents the dead branch in the register for later. |
| B | Also fix Reactivation now | Would require deciding *how* "booked" is detected (booking webhook? reply? PMS poll?) — a separate mechanism question — and expands scope. |

### Recommendation
Option A. Track the Reactivation dead branch as its own register item; revisit alongside a booking-detection
mechanism (analogous to, but distinct from, the confirmation-capture decision above).

---

## Summary of what's parked

| # | Decision | Depends on mechanism? | Recommendation |
|---|---|---|---|
| Mechanism | Option A (SMS reply) vs B (PMS-poll) vs Both | — | A first, then B as safety net (verify NexHealth sync) |
| 1 | Confirm reply keywords | Yes (only if A) | `YES, Y, CONFIRM, C, 1` (+ French if needed) |
| 2 | "CANCEL to cancel" wording | No (fix regardless) | Reword to drop CANCEL |
| 3 | Reactivation dead branch | No | Leave noted, fix Confirmation only |

*All parked pending owner decision. Nothing above is implemented yet.*
