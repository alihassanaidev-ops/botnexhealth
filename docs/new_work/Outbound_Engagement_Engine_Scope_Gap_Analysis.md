# Outbound Engagement Engine — Gap Analysis & Deep-Dive Critical Review (Consolidated)

**Companion to:** `Outbound_Engagement_Engine_Scope.md`
**Date:** 2026-07-01
**What this file is.** The single master review document for the scope. It has two parts:

- **Part I — Gap Analysis (decision record).** The de-duplicated review of all 25 gaps: what is
  solid, what is missing/under-specified, what the client has clarified (**Your Review**), and
  what has been validated and folded back into the scope (**My Findings**). This is the
  status-of-record for every gap.
- **Part II — Deep-Dive Critical Findings.** 16 investigation-backed findings that are either
  **genuinely new** or reach a **materially different conclusion** than Part I, each with its
  validation process documented. Cross-referenced to the relevant gaps.

*Supersedes the earlier standalone `…_Scope_Critical_Review.md` and `…_Deep_Dive_Investigations.md`
(both now removed).* Verified against the live codebase and the vendors' own docs (**NexHealth,
Twilio, Resend, Retell AI, FCC/TCPA sources**) where noted.

> **Status legend (Part I).**
>
> - 🔴 **Open — Critical** (blocks planning or breaks a campaign)
> - 🟠 **Open — Important** (operational/commercial mechanics needed to run safely at scale)
> - 🟡 **Open — Coverage** (in the original SOW or standard for a build this size)
> - ✅ **Resolved** (validated and **folded into the scope** — see the cited §)
> - ⏸️ **Deferred** (consciously out of v1; documented as a future enhancement)
> - ℹ️ **Note** (accuracy correction / smaller catch)
>
> Where a client clarification settled a gap, **Your Review** (the clarification) and
> **My Findings** (validation, research, resolution) are retained.

---
---

# PART I — GAP ANALYSIS (decision record)

## Overall verdict

The scope is **strong** on the engine (workflow model, triggers/actions, lifecycle), the
outbound agent (it correctly mirrors the inbound Retell → Backend → NexHealth → PMS chain),
multi-tenancy, the UX / visual builder, and the compliance posture.

It is **thin** on the things the campaigns actually stand on:

1. The **data foundation** — appointments, the patient roster, and leads. (Largely resolved
   into the event-driven read model; **lead intake remains net-new and open**.)
2. The **engine runtime** itself — there is **no durable, timezone-aware scheduler today**,
   which is the single largest net-new build and was previously mis-filed as "already solid."
3. Several **operational, commercial, and compliance mechanics** (outbound consent,
   frequency capping, provisioning, cost guardrails) that determine whether this runs safely
   at scale.

The verified root cause for #1: the platform today syncs only **reference data** (providers,
appointment types, operatories, descriptors) from NexHealth — **not patients and not
appointments**. Appointments are booked *through* to NexHealth and never persisted or queried
locally. So three of the four campaigns had no data source until the read model below was
added.

---

## Architecture decision summary (cross-cutting — applies to the data, scale, and messaging gaps)

- **Trigger discovery uses a thin, webhook-fed read model — endorsed by NexHealth itself**,
  which recommends *"replacing cron jobs with webhook subscriptions"*
  ([NexHealth webhooks](https://docs.nexhealth.com/docs/webhooks)). We keep a small,
  disposable projection of *upcoming appointments* and *recall-eligible patients* to decide
  whom to enroll; we do **not** mirror a full patient database. All booking/lookup at **action
  time stays real-time** (identical to inbound).
- **The NexHealth rate limit is a shared, per-key ceiling** (1,000 req/min on
  patient/appointment endpoints, 2,000/min otherwise) that **cannot be sharded per clinic on
  one partner key** ([rate limiting](https://docs.nexhealth.com/reference/rate-limiting)) — so
  the read model is what makes outbound viable at scale. A **multi-key** model (one key per
  DSO) could raise this ceiling, but is **vendor-unconfirmed** (see Gap 7), and **send-time
  pacing is required regardless** (see Gap 8).
- **Messaging isolation uses vendor-native multi-tenant patterns:** Twilio **sub-accounts per
  clinic**, Resend **per-clinic sending domains**, and **per-workspace Retell + BYO-SIP**
  telephony (see Gap 9).

---

## Data foundations

### 1. Appointment data foundation — ✅ Resolved *(folded into scope §3.5)*
*(was Critical; sources: this analysis + codebase verification)*

Confirmation and Reminder are driven entirely by "an upcoming appointment exists," but the
platform keeps **no local appointments** — it books through to NexHealth and retains nothing,
and the sync service does not pull appointments.

**Your Review.** We shouldn't persist appointments. Booking should be real-time through the
API exactly like the inbound agent; the inbound agent keeps no local appointment/availability
cache.

**My Findings.** Correct for *action-time* data — our own docs confirm "slot search always
hits the live API." The one refinement: Confirmation/Reminder are *system-initiated*, so the
engine must *discover* upcoming appointments before anyone is on the line. That is served by a
**thin, webhook-fed projection** (a derived cache, not a system of record), re-validated live
at send time. Operational specifics now in the scope:

- NexHealth appointment webhooks are `appointment_created` / `appointment_updated` /
  `appointment_requested` (+ async `appointment_insertion.*`), HMAC-SHA256 signed, with
  retries over ~48h then deactivation, **no built-in dedup**, and **ordering not guaranteed**
  ([webhooks](https://docs.nexhealth.com/docs/webhooks)).
- **There is no dedicated "cancelled" event** — cancellations arrive as `appointment_updated`
  (status change), so the processor evaluates status on every update.
- Webhooks are **go-forward only**, so the working set is **seeded by an initial REST
  backfill** and kept honest by a **paced reconciliation sweep**; endpoint health is monitored
  (a >48h outage forces re-subscribe + backfill). We own webhook **idempotency**.

Net: same freshness the SOW assumed, achieved event-driven, with **fewer** NexHealth calls
than polling/sync. *(See Part II Finding 4 for the per-location subscription lifecycle this
implies at scale.)*

### 2. Patient roster / recall eligibility — ✅ Resolved *(folded into scope §3.5 / §10.3)*
*(was Critical)*

Contacts are created **only from inbound calls** today; Overdue Recall and bulk campaigns need
the clinic's patient population, which lives in the PMS and is never synced.

**Your Review.** We shouldn't maintain or synchronize a full patient database; patient/contact
info should be fetched dynamically at runtime via the APIs.

**My Findings.** Right for action-time lookups. For Recall — a *population* question — we
source eligibility from **NexHealth's own recall data** via a **scheduled, paced, off-peak
pull** per clinic (`GET /patient_recalls` filtered by `due_after`, sorted by `date_due`;
`GET /recall_types`), held only as a transient eligibility list
([recalls](https://docs.nexhealth.com/reference/patient-recalls-1)). Identity/contact details
for an attempt are fetched real-time. **Caveat (was IGR #14):** there is **no boolean
"overdue" field** — the engine must derive it from due-date math, and recall data is limited to
the PMSs that support it (OpenDental/Dentrix/Eaglesoft/…); handle PMSs where it is unavailable.

### 3. Lead intake & management — ⏸️ Deferred
*(this analysis #3 + independent review #8)*

**Your Review (2026-07-01).** Deferred for now. Dental clinics are not currently running
ads or generating leads in a way that makes this feature necessary at this stage; revisit
later if that changes. Note: Sales Qualification (launch campaign #4) depends on this — with
lead intake deferred, that campaign has no data source for net-new (never-called) prospects
and is effectively deferred with it. Manual/CSV enrollment of *existing* contacts still works.


Sales Qualification "receives inbound leads," but the scope never defines **where leads come
from**, what a **lead** is as an entity, or how source/status/attribution is tracked. The
trigger exists; the substrate does not. Verified: there is **no `Lead` model, no
web-form/CSV/manual intake, no enrollment concept** — `Contact` rows are auto-created *only* by
the post-call pipeline; the Contacts API exposes list/detail/reveal/merge/unmerge but **no
create or import**.

**Concrete example.** A clinic runs an Invisalign ad; a prospective **new** patient (never
called) fills a web form. You want the AI to call, qualify, and book them. Today there is **no
front door** for that person to enter the system — so the "new lead arrived" trigger can never
fire. Sales Qualification is the one launch campaign whose audience hasn't called yet, so it
alone has no data source.

**What the scope needs (net-new):**
- a **lead intake** path: web-form endpoint / inbound webhook / CSV import / manual add;
- a **lead entity or lead-state on `Contact`** — a lead is not yet a NexHealth patient (no
  `nexhealth_patient_id`), so a lifecycle is needed: lead → qualified → PMS patient on booking;
- **dedup** against existing contacts/patients;
- **consent capture at creation** (a web-form lead must consent — TCPA/CASL; you can't
  cold-call a scraped list). See Gap 17 for the bulk-CSV variant.

---

## Engine & scheduling

### 4. Durable, timezone-aware scheduler — TZ model ✅ Resolved / durable engine 🔴 Core build
*(was "🔴 Open — Critical." Two things were tangled under one item; separated 2026-07-01.)*

The codebase has exactly two scheduling mechanisms: **Celery** for *immediate* async work
(no `beat`, no `eta`/`countdown`) and **EventBridge-cron → Fargate** batch scripts. **Nothing
can durably schedule "send to this patient at 9am clinic-time in 3 days" and survive
restarts.** Every core primitive — wait-until-offset, wait-for-reply-or-timeout, quiet-hours
hold, resumable runs, **exactly-once step dispatch** — needs a **durable timer + workflow
state machine** (a Temporal-style runtime, or a timer table with a leader-elected poller).

**Your Review (2026-07-01).** This is **not** about per-patient timezones. Each clinic has its
own configured timezone stored in clinic settings; every workflow runs relative to **that
clinic's** timezone. Concretely: when a contact is enrolled (e.g. because a Callback custom
field was populated), the workflow reads the callback date/time, **waits until that exact
moment in the clinic's timezone**, then fires the configured action (HTTP request, place call,
etc.). Timezone handling, wait steps, and condition matching all live **inside the workflow
engine**.

**My Findings — the design is correct; separating what's *decided* from what's *built*.**

- **(a) Timezone model — ✅ Resolved (already in scope §8).** Clinic-level TZ is the right,
  simpler v1 choice, and each `InstitutionLocation` already stores a timezone — adopt it. The
  "wait until the exact local time, then act" model described above is exactly how a workflow
  engine should behave. This also settles Gap 18. Nothing further to decide here.
- **(b) Durable scheduler engine — 🔴 the central net-new build (core work, not an open
  design gap).** "Decided" is not "built." The machinery that parks a step for days/weeks and
  fires it **exactly once** after deploys, restarts, and crashes **does not exist today** —
  only immediate Celery and cron→Fargate batch, neither of which can durably wait-and-resume.
  This is the single largest build in the project, and it is the **same runtime** the
  DSL/interpreter needs (Gap 5) — one engine, not two. Tracked as core engine work (§5 / §14),
  not as an unresolved design gap.
- Two properties this build must carry: **DST correctness** (compute fire times *in* the
  timezone, never a fixed UTC offset — "9 AM next month" can cross a DST boundary), and
  **jitter/smoothing** so a shared "9 AM" across many clinics doesn't stampede the NexHealth
  budget and Retell pool at once (ties to Gap 8's Problem B).

### 5. Workflow-DSL + compliance-validation engine — 🟠 Open — Important (core, not optional)
*(clarified 2026-07-01)*

The visual builder has two layers. The **visible** layer is the canvas (drag-and-drop steps,
branches, waits). The **hidden** layer — the hard, risky part — is three things behind it:

1. a **workflow-definition schema** — the format a saved workflow is stored in;
2. an **interpreter / execution engine** — reads that definition and actually runs it (this is
   the **same runtime** as the durable scheduler in Gap 4; one build, not two); and
3. a **compliance-aware validation layer** — checks a workflow is legal *before* publish:
   every branch reachable, merge fields valid, **no send step without a consent path**,
   mandatory quiet hours present.

**Why this is required, not optional.** This is the engine itself — without it there is no
"configuration, not code." The scope's own §5.1 states *"adding a campaign type must not
require a code release"*; **that promise is only true if the schema + interpreter + validator
above exist.** (Nothing new is being proposed here — this is a hidden requirement implied by
the scope's own words, not a new campaign feature.) The scope undersold it as "templates" +
"validation and guardrails"; the interpreter and validator — not the canvas — are the
expensive part, and they overlap almost entirely with the durable-engine build in Gap 4.
*(Part II Findings 1–2 add a concrete validator requirement: enforce a content class per
campaign so an exempt campaign can't be silently turned into unlawful telemarketing.)*

### 6. Two-way conversation handling — ⏸️ Deferred (v1)
*(this analysis #4)*

SMS is described as "two-way" with a "wait-for-response" primitive, but the scope never defines
how a free-text reply ("yes" / "can we do Thursday?") is interpreted (NLU beyond STOP/START)
and correlated back into a *specific* running sequence.

**Your Review.** A dedicated per-clinic conversational SMS agent is out of scope for v1.
Instead, surface an in-app notification/alert when a patient replies, so staff can continue
manually.

**My Findings.** Accepted — v1 surfaces inbound replies as a **staff notification** via the
existing in-app + SSE channel; the autonomous conversational agent is a later phase. Existing
STOP/opt-out keyword handling stays in place. (Reduces v1 scope; the NL reply-routing work is
deferred.)

---

## Integration & scale

### 7. Tenant-owned NexHealth integration — 🔴 Open (vendor-unconfirmed)
*(independent review #1)*

The scope's lead recommendation proposes each clinic/DSO "connect and manage its own NexHealth
account and credentials… an extension, not a re-architecture," citing the
`nexhealth_api_key_encrypted` column.

**Your Review.** The single-account design is just an original assumption. I want each tenant
(clinic, or per-DSO) to use its **own purchased NexHealth account + API key**, stored in our
backend, with routing handled server-side. I believe this is **~95% feasible** on our side.

**My Findings — partially validated; the engineering is close, the vendor model is not as
assumed.**

*(A) Our code is closer to ready than first reported.* The rate limiter **already shards by
key** (`rate_limit.py:207`, windows keyed `…:{api_key_id}:…` — two keys get **independent**
budgets automatically); `AuthConfig`/HTTP client are per-key (`client.py:40-54`); and the
dormant `nexhealth_api_key_encrypted` column exists. Contained changes needed:
1. `NexHealthAdapter.create` hardcodes `global_settings.nexhealth_api_key` (`adapter.py:135`)
   → prefer the tenant's decrypted key.
2. **The token cache is a singleton** (`RedisTokenCache` key `"nh:token"`, lock
   `"nh:token:refresh-lock"`, `token_manager.py:84,129`) → with multiple keys this leaks
   tenant A's bearer token to tenant B. The cache + lock keys MUST include the api-key hash.
   Small, mandatory, easy to miss.
3. **The client is a process singleton** (`adapter.py:147-151`) → move to a bounded **keyed
   client pool** so N tenants don't exhaust sockets/FDs.
So "~95% feasible" is fair for the **engineering of a multi-KEY model**, provided #2 is done.

*(B) NexHealth's model breaks the proposal as worded.* NexHealth is **partner-owned key +
per-location grants**, with **no OAuth / bring-your-own-account / marketplace** flow — "each
clinic buys its own NexHealth API account and we connect to it" is **not supported**
([auth](https://docs.nexhealth.com/reference/authentication-1)). Whether a partner may hold
**many production keys**, and whether they get **independent budgets** (vs. an account-level
aggregate), is **UNCONFIRMED** — the make-or-break question. Production access is sales/BAA-
gated per relationship, and billing tracks practices-onboarded + call volume, so N accounts
likely **add cost without reducing usage spend**.

**Conclusion — reframe, don't discard.** Drop "clinic-owned accounts"; the viable kernel is
"partner holds multiple keys (one per DSO) to shard the limit," gated by written NexHealth
confirmation (developers@nexhealth.com) on (1) multiple production keys and (2) independent
per-key budgets. Until then the event-driven read model is the single strategy. (Directly
determines Gap 8.)

### 8. NexHealth rate limit + send-time pacing — 🔴 Open — Critical
*(independent review #6 + the architecture summary)*

The scope's "~1000 requests/minute across the entire platform" is imprecise: it's **per partner
key**, tiered (~1,000/min patient+appointment, 2,000/min others, 100 req/s global —
[rate limiting](https://docs.nexhealth.com/reference/rate-limiting)).

**Does the multi-key idea (Gap 7) fully fix this? No — it helps but isn't sufficient.**
- **Problem A — one shared bucket (today):** all clinics share one key's 1,000/min. Multi-key
  fixes this (each DSO gets its own budget; our limiter shards per key for free) — **conditional
  on Gap 7's vendor confirmation**.
- **Problem B — pouring too fast into one bucket (remains):** even with its own key, a clinic
  firing "day-before reminders for 800 patients at 9:00 AM" (each a live re-validation + maybe
  a booking read) can blow past its **own** 1,000/min that minute.

Analogy: give everyone their own sink (multi-key) **and** still turn the tap to a steady flow
(**pacing**). So regardless of multi-account, the engine needs **send-time pacing** — spread
each campaign's sends across its window with jitter, queue against the per-key budget, back off
on 429. §12's rate-limit bullet is thin on this. *(Part II Finding 13 quantifies how send-time
re-validation feeds Problem B.)*

### 9. Per-tenant messaging & telephony infrastructure (+ A2P / Canada) — ✅ Resolved *(folded into scope §3.5 / §7.2 / §11)*
*(this analysis #8 + independent review #4 [messaging/Canada] + #7 [Retell])*

Verified current reality: **single shared Twilio account** (only `twilio_from_number` per
location; no per-tenant SID/sub-account columns); **Resend fully shared** (no per-tenant key
or sending-domain columns); **Retell inbound-only**, single shared workspace, agent
provisioning 100% manual. So this is net-new infra, not "already isolated."

**Your Review.** Build it from scratch. Each clinic configures/manages its own Twilio account;
the backend stores credentials securely; for email, use Resend per-account. (And: does Canada
need A2P-style registration?)

**My Findings — endorsed, with vendor-native refinements:**
- **Twilio:** use **per-clinic sub-accounts** (Twilio's recommended ISV "Type #1" model —
  Secondary Customer Profile + **Brand + Campaign per business**), not standalone accounts;
  isolates compliance blast radius and shards throughput
  ([Twilio ISV A2P](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/onboarding-isv)).
  Full BYO-account stays an option for clinics that insist on owning billing.
- **Email:** prefer **per-clinic sending domains on the platform Resend account** (per-domain
  SPF/DKIM/DMARC + custom return path) as default; per-clinic SMTP optional
  ([Resend domains](https://resend.com/docs/add-a-domain)). Budget for DNS onboarding +
  **reputation warm-up** per domain. *(Part II Finding 6: warm-up is a launch-gating,
  business-outcome issue, not a footnote.)*
- **Retell telephony:** Retell concurrency is **per-workspace, not per-account**, and one
  account can hold **many workspaces** — so **workspace-per-tenant (or per-DSO)** shards
  concurrency **without an Enterprise contract** (Enterprise advertises "no cap" but is
  contact-sales). **BYO-telephony via SIP** binds each clinic's own Twilio sub-account numbers
  to its workspace ([concurrency](https://docs.retellai.com/deploy/concurrency),
  [custom telephony](https://docs.retellai.com/deploy/custom-telephony)). **Correction (Part II
  Finding 5):** Retell **does dial Canada natively** — BYO/SIP is *not* required for Canadian
  outbound (only for unsupported destinations or own-telephony preference).
- **A2P registration is region-specific.** **US:** A2P 10DLC Brand + Campaign per business
  (campaign approval ~10–15 days — a real onboarding gate). **Canada has NO 10DLC equivalent**
  (TCR is a US construct); the practical requirement is **Twilio toll-free verification**
  (covers Canada, ~3–5 business days) plus **CASL** (consent, sender identification,
  STOP/unsubscribe within the statutory window, bilingual EN/FR keywords). A Canada-first
  clinic can go live in ~1–2 weeks; the longer US cycle applies only when serving US patients.
  *(Part II Finding 11: the Jan-2026 toll-free BRN mandate is now in force — audit it.)*
- Store all per-tenant credentials encrypted, following the `nexhealth_api_key_encrypted`
  pattern.

*(One item to confirm with Retell: whether multiple distinct Twilio **parent** accounts under
one Retell account is supported, and whether a workspace is a guaranteed PHI/BAA isolation
boundary.)*

### 10. No-PMS tenants cannot run 3 of the 4 campaigns — 🟠 Open
*(independent review #10)*

Confirmation, Reminder, and Recall all depend on PMS appointment webhooks + recall lists;
Sales Qualification cannot even **book** without NexHealth write-back. No-PMS mode is an
explicit roadmap state (`feat/native-pms-mode`), yet the scope never defines the outbound
surface for those tenants (likely SMS/email blast + qualification-without-booking only).

### 11. Clinic capacity awareness — ✅ Resolved
*(this analysis #11)*

**Your Review.** Already addressed by the real-time API approach — availability is always
fetched live from NexHealth, so the agent books against current availability, not stale cache.

**My Findings.** Agreed — because booking hits **live NexHealth slots** and the PMS rejects
conflicts, stale-data overfill is not a concern. The only residual is **outreach throughput**
(calling many patients vs. front-desk capacity), handled by the **per-clinic concurrency
limits** already in scope (and Retell concurrency is itself a capped, purchased resource —
[Retell pricing](https://www.retellai.com/pricing)). No appointment cache needed for capacity.
*(Part II Finding 7: that concurrency limit is also a hard throughput ceiling + cost line.)*

---

## Compliance & consent

### 12. Outbound consent model (voice + email) — 🔴 Open (partially; client prefers to defer)
*(this analysis #6 + independent review #3)*

Consent today is **SMS-only by construction**: `ConsentChannel` has a single `SMS` value behind
a `CHECK (channel IN ('sms'))` constraint on `ConsentRecord`/`SmsSuppression`/`DoNotContact`;
**email through Resend performs zero consent/suppression/unsubscribe checks.**

**Your Review.** Prefer to build core functionality first and address consent management toward
the end. For SMS, GoHighLevel-style STOP keyword opt-out; for voice, use Retell conversation
tags → set a DNC flag.

**My Findings — mostly validated, with two corrections that should not be fully deferred:**
1. **SMS STOP is already built** (inbound-SMS webhook + `SmsComplianceService`); Twilio also
   enforces STOP at the carrier level. Keep it. ✅
2. **Voice via Retell tags → DNC** is feasible and cheap (post-call pipeline already tags
   calls); treat the LLM tag as best-effort and add a manual staff DNC toggle. (Canada: cold
   recall/sales calls can fall under CRTC National DNCL; reminders to existing patients are
   generally exempt.)
3. **Email needs an unsubscribe mechanism — this is the one piece that is a legal minimum, not
   safely deferrable.** CASL and CAN-SPAM require a working unsubscribe + suppression on
   commercial email (recall/sales). Build an unsubscribe link + token + email suppression list.
- **Structural:** extend `ConsentChannel` (+ CHECK) to `voice`/`email` before any of the above
  is persistable (small migration). And note **STOP/tag is opt-OUT, not opt-IN** — CASL requires
  consent **before** commercial sends, so the clean path is **express consent capture at patient
  intake** once (covers reminders, recall, marketing); keyword/tag handles withdrawal. Pure
  appointment confirmations/reminders to existing patients are largely CASL-exempt.

Net: opt-out ≈ SMS done / voice cheap / **email unsubscribe is the real build**; multi-channel
consent schema + intake consent are required for commercial campaigns even if formal consent
management is otherwise deferred. **⚠️ See Part II Findings 1–3: US TCPA (AI-voice ruling) makes
opt-out *insufficient* for Recall and Sales Qualification, and makes the ≤3/week frequency cap a
condition of legality — materially stronger conclusions than "defer to the end."**

### 13. Suppression scoping (per-location) — ✅ Resolved *(folded into scope §11)*
*(independent review #9)*

**Your Review.** On STOP, store a flag so no further SMS/calls go to that contact. Suppression
should be **per location, not per institution** — even within one DSO a patient may still want
messages from another location they visit.

**My Findings.** Per-location is the right default and is **consistent** with the per-tenant-
number model (each location sends under its own number/brand; STOP applies to the sender; Twilio
enforces STOP per number-pair, itself per-location). Adopted in the scope, with these decisions
captured: STOP suppresses **all** channels for that location; records carry **channel +
location**; a privileged staff action can set an **institution/DSO-wide** DNC for patients who
ask to be removed everywhere. Open sub-decisions to lock down in build: (a) per-(location) vs
per-(location, channel) granularity, (b) verify/migrate whether today's `DoNotContact`/
`SmsSuppression` is institution- or location-scoped, (c) reassigned-number hygiene (see Gap 19).

### 14. Global frequency capping / contact-fatigue policy — ⏸️ Deferred
*(this analysis #7)*

A patient can match Confirmation **and** Reminder **and** Recall and be contacted repeatedly.
The scope handles suppression on opt-out/goal but not **proactive per-contact frequency caps,
cooldowns, and cross-campaign mutual exclusion**.

**Your Review.** Important, but postpone until after core outbound is complete; keep documented.

**My Findings.** Deferred, documented as planned. *Risk while deferred:* overlapping campaigns
can over-contact a patient; mitigate operationally by enabling few campaigns initially.
**⚠️ Reconsider (Part II Finding 3):** the ≤3/week/provider cap is a *condition* of the TCPA
healthcare exemption — deferring it can push Confirmation/Reminder out of compliance, so a basic
cap likely belongs in v1.

### 15. Multi-language / localization — ⏸️ Deferred (English-only v1)
*(this analysis #5)*

Target markets are Canada and the US; Quebec (French) and large US Spanish populations make
bilingual voice/SMS/email a probable longer-term requirement.

**Your Review.** Out of scope; English only for now.

**My Findings.** Removed from v1, documented as a future enhancement (spans agent prompts,
templates, content governance, and per-contact language selection if later pursued).

### 16. Patient preference center — 🟡 Open (future)
*(this analysis #15)*

Only full opt-out is implied. A **channel-preference / opt-down** model ("texts yes, calls no")
improves compliance and retention. Related to Gap 12 (consent) and Gap 13 (suppression).

### 17. Bulk CSV enrollment — consent provenance + PHI handling — 🟠 Open
*(independent review #13)*

Uploading a contact list is **not** consent under TCPA/CASL. The CSV flow (§5.3 / §9.2) must
also resolve uploaded identities to NexHealth patients and securely handle an uploaded **PHI
file** (encryption, retention, audit, isolation). None of this is addressed. Ties to Gap 3
(lead intake).

### 18. Per-patient timezone — ✅ v1 resolved (clinic-level), US refinement noted
*(independent review #12)*

TCPA quiet hours follow the **called party's** location, not the clinic's; patient timezone is
not stored (area-code inference is unreliable). The client confirmed v1 uses **clinic-level**
timezone (Gap 4) — a reasonable launch posture. **Revisit as a US-market refinement** for
known out-of-region patients. **⚠️ Part II Finding 12 sharpens this:** for US clinics with
out-of-region patients, clinic-level TZ is a *compliance defect*, not just a refinement.

---

## Operations & commercial

### 19. Sender / number provisioning workflow — ✅ Resolved (with Gap 9)
*(this analysis #8, provisioning aspect)*

Twilio numbers are configured **manually** per location today. The per-tenant sub-account model
(Gap 9) defines the target; what remains is making **provisioning + 10DLC/toll-free
registration an explicit onboarding workflow** with an owner. Smaller catches that belong to
dialing live here: **answering-machine detection**, **wrong/reassigned-number** suppression
(TCPA reassigned-number risk), and **deceased-patient** suppression. *(Part II Findings 4, 6, 11
quantify the compounded, multi-week onboarding latency this workflow must absorb.)*

### 20. Usage metering & cost controls — ✅ Resolved *(in scope §9.4 / §12)*
*(this analysis #9)*

**Your Review.** Very important. Aggregate usage from **Retell + Twilio** (inbound + outbound),
surfaced per clinic/location, per institution/DSO, and combined.

**My Findings.** In scope. Retell bills per connected minute + **$0.005/dial** for batch
dialing (concurrency 20 free, then $8/concurrent/mo); Twilio bills per segment/minute, with
sub-accounts giving natural per-clinic attribution; call `metadata` enables tagging
([Retell pricing](https://www.retellai.com/pricing),
[Twilio ISV](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/onboarding-isv)).
Capture per-interaction usage tagged with location/institution and **aggregate up
location → institution → DSO**. Optional budget caps/throttling can layer on later. **⚠️ Part II
Finding 10: spend/blast-radius caps should be a launch control, not "optional later," given a
democratized engine that spends money.**

### 21. Holiday & clinic-closure calendar — ⏸️ Deferred (manual pause v1)
*(this analysis #10)*

Quiet/operating hours are weekly-recurring; one-off closures and public holidays (which differ
by country and province/state) are omitted.

**Your Review.** For v1, a clinic pauses or drafts the relevant workflow for a holiday/closure;
a managed calendar is a future enhancement.

**My Findings.** Accepted — v1 relies on **manual pause/draft**; quiet/operating hours still
apply automatically, only one-off closures need the manual step.

---

## Coverage & quality

### 22. Testing / QA & sandbox — 🟡 Open
*(this analysis #12)*

The original SOW had an explicit testing section: unit tests, **NexHealth sandbox** integration
tests, end-to-end booking/confirmation, **load testing** of scheduling/queue processing, and
**tenant-isolation regression**. The current scope folds this into a single reliability bullet;
it deserves to be restated as its own quality section.

### 23. Rollout / phasing / data backfill — 🟡 Open
*(this analysis #13)*

No **delivery sequencing** (engine → first channel → first campaign), no **per-tenant
enablement plan**, and no mention of **backfilling existing appointments/patients** when a
clinic turns the engine on (the read model's initial backfill, Gap 1). *(Part II Finding 16:
this directly contradicts the scope's "production-grade day one, no hardening phase" claim.)*

### 24. Analytics definitions & export (incl. "attributed revenue") — 🟡 Open
*(this analysis #14 + independent review #11)*

The scope doesn't define **what counts as a conversion** (confirmed? recall booked?),
**attribution windows**, or whether clinics can **export** results. Specifically, **"attributed
revenue" is internally contradictory**: §9.4 lists it as a core analytics deliverable, but its
only credible source — NexHealth **financials** — is deferred to §13 ("future"). As specified it
cannot ship at launch; resolve the §9.4 vs §13 contradiction.

### 25. Message / script content governance — 🟡 Open (future)
*(this analysis #16)*

"Approved merge fields" are mentioned, but not **who approves agent scripts and templates** —
relevant for healthcare-messaging review and brand safety. **⚠️ Part II Finding 2 gives this a
legal edge:** free-text authoring can silently void the TCPA healthcare exemption or leak PHI,
so the validator must enforce content class + PHI guards, not just brand review.

---

## Smaller catches & accuracy notes

- ℹ️ **"Goal" should be a first-class workflow concept** (exit condition), not just an action.
- ℹ️ **Dial edge cases** — answering-machine detection, wrong/reassigned number (TCPA
  reassigned-number risk), deceased-patient suppression (also referenced in Gap 19).
- ℹ️ **Write outreach back to the PMS** as a communication/contact log (NexHealth supports
  this) for staff visibility into automated outreach.
- ℹ️ **Time-to-execute expectation** for a triggered enrollment (how promptly a due step fires).
- ℹ️ **Accuracy — requested callback time *is* stored today** *(independent review #15)*: §3.3
  says it "is not stored in a dedicated field." The codebase has `Call.preferred_callback_datetime`
  (+ `callback_resolved`/`_at`/`_note`); the callback queue is derived from
  `Call.call_status == 'needs_callback'`, not a dedicated table. Correct §3.3; the field the
  AI-callback path needs already exists.

---

## What is already solid (no rework needed)

- The **Retell AI → Our Backend → NexHealth → Dental PMS** chain and the outbound agent
  reusing it, including the **idempotency** wrapper `(call_id, function_name, HMAC(args))`
  *(but see Part II Finding 8: this call-scoped key does not cover scheduled-step dispatch)*.
- The **multi-tenant execution-context** framing (Institution Group → Institution → Location;
  per-location agent/binding/identity/timezone/isolation on Postgres RLS).
- The **triggers/actions classification** (Critical / Recommended / Optional).
- The **visual workflow builder** UX and the draft/publish/versioning model *(the builder's
  hard part — the DSL/validation engine — is Gap 5, not the canvas)*.
- The **reliability posture** for idempotency, dead-letter + replay, and circuit breakers
  *(note: "distributed scheduling" is **not** solid — it is net-new, Gap 4)*.
- **Re-validating live at send time** so stale read-model data never causes a wrong contact
  *(cost of this is Part II Finding 13)*.
- The corrected **callback** distinction (captured + manual today → optional AI automation via
  the outbound agent + workflow engine).

---

## Recommendation & net effect

**Open issues to resolve before design freeze:**
1. **Lead intake & model** (Gap 3) — deferred per client; note Sales Qualification defers with it.
2. **Durable, timezone-aware scheduler** (Gap 4) — the engine itself; nothing like it exists.
3. **NexHealth multi-key question + send-time pacing** (Gaps 7, 8) — get vendor confirmation in
   writing; build pacing regardless.
4. **Outbound consent** (Gap 12) — at minimum, **email unsubscribe/suppression** and a
   multi-channel consent schema; capture express consent at intake for commercial campaigns.
   **Elevated by Part II Findings 1–3: written consent for Recall/Sales Qualification and a
   basic frequency cap are compliance-blocking, not deferrable.**
5. Restate **testing/QA** (Gap 22), **rollout/backfill** (Gap 23), and **analytics
   definitions** (Gap 24) as their own sections; resolve the attributed-revenue contradiction.

**Resolved and folded into the scope:** appointment read model (Gap 1), recall eligibility
(Gap 2), per-tenant messaging + telephony + A2P/Canada (Gap 9), capacity awareness (Gap 11),
per-location suppression (Gap 13), clinic-level timezone (Gap 18), provisioning target (Gap 19),
usage metering (Gap 20).

**Deferred to a later phase (documented):** lead intake (Gap 3), two-way conversational agent
(Gap 6 → staff notification), frequency capping (Gap 14 — but see Part II Finding 3),
multi-language (Gap 15), preference center (Gap 16), holiday calendar (Gap 21), content
governance (Gap 25).

**Net effect on v1:** reduced (conversational agent, language, formal consent management beyond
opt-out + email unsubscribe, frequency capping, holiday calendar, capacity caching); reframed
(data foundation → thin webhook-fed read model + recall lists, **fewer** PMS calls); and
added/endorsed (per-clinic Twilio sub-accounts + Resend domains + Retell workspaces, usage
metering across Retell + Twilio).

**Decisions still needed from product:**
- NexHealth: confirm whether a partner may hold **multiple production keys with independent
  budgets** (Gap 7), and whether there is a **cap on webhook subscriptions per partner** (Part II
  Finding 4).
- Confirm whether **email unsubscribe** is accepted as the non-deferrable consent minimum
  (Gap 12).
- **Legal classification of each campaign** (exempt-healthcare vs. marketing) to settle the
  consent basis for Recall and Sales Qualification (Part II Findings 1–3).
- Default **callback** posture per clinic (manual queue vs. AI outbound) and whether to capture
  a preferred callback time as structured input.

---

## Appendix — verification basis (Part I)

**Codebase (verified):** reference-data sync only (no patient/appointment sync); SMS-only
`ConsentChannel`/`CHECK` constraint; email path has no suppression; no Celery `beat`/`eta`; no
durable timer or workflow state; `nexhealth_api_key_encrypted` present but unused (adapter uses
global key); the NexHealth client stack is already key-parameterized (rate limiter keys on
`hash_api_key`; per-config `AuthConfig`) but the token cache + client are process singletons;
single Twilio account, only `twilio_from_number` per location; no per-tenant Resend creds; no
`Lead` model / no contact-create / no CSV import; callback queue derived from `Call` status with
`preferred_callback_datetime` present; Retell integration is inbound-only (no `create_phone_call`);
`Contact.email_encrypted` exists but is opportunistically populated, not guaranteed.

**External (verified against official docs):**

- **NexHealth:** appointment webhooks (created/updated/requested; **no dedicated cancel
  event**), HMAC-signed, at-least-once with ~48h retry-then-deactivate, **backfill required**;
  `GET /patient_recalls` + `due_after` exposes "who is due" (no boolean overdue); rate limit
  **per key**, tiered (~1,000/min patients+appts, 2,000/min others, 100 req/s); **no OAuth /
  bring-your-own-account model**.
- **Twilio:** sub-accounts are the recommended ISV multi-tenant model (~1,000 default cap); A2P
  10DLC per business (Secondary Customer Profile → Brand → Campaign), **US campaign approval
  ~10–15 days**; **Canada has no 10DLC equivalent** — toll-free verification (covers Canada) +
  CASL.
- **Resend:** per-tenant **sending domains** with per-domain SPF/DKIM/DMARC + custom return
  path (multi-tenant pattern).
- **Retell:** `POST /v2/create-phone-call` supports outbound; **concurrency is per-workspace**
  (default 20, ~$8/concurrent-call/mo); multiple workspaces per account; BYO-telephony/SIP
  imports external Twilio numbers; agents creatable via API
  (`create-agent`/`update-agent`/`publish-agent`). *(Correction vs. earlier note: Retell dials
  Canada natively — Part II Finding 5.)*

---
---

# PART II — DEEP-DIVE CRITICAL FINDINGS

**Scope of this part.** Findings are ordered by threat to the product. The **primary set (1–11)**
is investigation-backed and either **genuinely new** or reaches a **materially different
conclusion** than Part I. The **secondary set (12–16)** are additional meaningful scope
observations (some sharpen, rather than repeat, a Part I gap — flagged where so).

**Method.** Doubt-driven. Each concern was validated against official/primary sources (FCC ruling
text, healthcare-TCPA counsel summaries, NexHealth/Retell/Resend/Twilio docs) before inclusion.
Unverifiable items are flagged as vendor/legal-confirmation-required, not asserted. Each finding
documents: **Concern → Why it flagged → Investigation & validation → Conclusion → Recommendation.**

## Priority summary

| # | Finding | Severity | Relation to Part I |
|---|---|---|---|
| 1 | AI-voice consent is content-dependent; opt-out is insufficient for Recall & Sales Qualification | 🔴 Legal | **Materially different** vs. Gap 12 |
| 2 | The product design actively pushes Recall toward *exemption-voiding* content (+ PHI-in-SMS) | 🔴 Legal/systemic | **New** (consent + templates + validator) |
| 3 | The healthcare-exemption frequency cap (≤3/week) is a condition of legality, not a deferrable nicety | 🔴 Legal | **Materially different** vs. Gap 14 |
| 4 | NexHealth webhook subscriptions are per-subdomain → per-location subscription lifecycle at scale | 🟠 Integration | **New** (concrete, validated) |
| 5 | Factual error: Retell dials Canada natively; BYO-SIP is *not* required for Canadian outbound | 🟠 Accuracy | **New** — corrects an accepted "fact" in Part I / scope |
| 6 | Per-clinic cold email domains spam-folder reminders on day one (silent business failure) | 🟠 Deliverability | **Deeper/different** than Gap 9 |
| 7 | Voice concurrency is a hard throughput ceiling + per-workspace cost multiplier | 🟠 Scale/cost | **New** quantified concern |
| 8 | The "already-solid" idempotency key is call-scoped; doesn't cover scheduled-step dispatch | 🟠 Reliability | **New** — contradicts a "solid" claim |
| 9 | Version-pinning has no compliance kill-switch for in-flight runs | 🟡 Compliance ops | **New** |
| 10 | No spend / blast-radius controls on a money-spending, patient-contacting democratized engine | 🟡 Operational safety | **New** |
| 11 | Jan-2026 toll-free BRN mandate (now passed) may already block registrations | 🟡 Time-sensitive | **New**, actionable now |
| 12 | Clinic-level quiet hours are a TCPA liability for US cross-timezone clinics | 🟠 Legal | **Sharper conclusion** than Gap 18 |
| 13 | Send-time re-validation reintroduces per-contact NexHealth calls at the burst moment | 🟡 Scale | **New** mechanism insight (relates to Gap 8) |
| 14 | Cross-channel fallback (voicemail→SMS) crosses consent domains and assumes mobile | 🟡 Logic | **New** |
| 15 | "Four launch campaigns" vs. deferred lead intake → effectively three | 🟡 Consistency | Consequence of Gap 3 deferral |
| 16 | "Production-grade day one, no hardening phase" vs. the net-new build size | 🟡 Phasing | Sharpens Gap 23 |

---

## PRIMARY SET — investigation-backed, new or materially different

## Legal / compliance

### 1. AI-voice consent is content-dependent; opt-out is insufficient for Recall & Sales Qualification

- **Concern.** The scope's consent model (§11) and Gap 12 accept an **opt-out** posture (SMS
  STOP; Retell voice tags → DNC) as sufficient. The whole product is an *AI voice* agent.
- **Why it flagged.** "AI voice + opt-out only" contradicted the 2024 FCC action on AI voices. If
  AI voice is a regulated "artificial voice," opt-out may not be a lawful basis for the
  marketing-flavored campaigns.
- **Investigation & validation.**
  - FCC **Declaratory Ruling, Feb 8, 2024 (FCC 24-17, Docket 23-362)**: AI technologies that
    generate human voices **are** "artificial or prerecorded voice" under the TCPA; such calls
    require **prior express consent** (or **prior express *written* consent** for marketing),
    plus caller-identity disclosure and opt-out.
    ([FCC](https://www.fcc.gov/document/fcc-confirms-tcpa-applies-ai-technologies-generate-human-voices),
    [FCC-24-17](https://docs.fcc.gov/public/attachments/FCC-24-17A1.pdf))
  - Healthcare-exemption 3-factor test (validated via counsel summaries): the call must
    (a) concern an **inarguably health-related** product/service, (b) go to a patient with an
    **established treatment relationship**, and (c) concern the patient's **individual healthcare
    needs** — and contain **no telemarketing/solicitation/advertising**.
    ([Bass Berry](https://www.bassberry.com/news/tcpa-exemptions-for-healthcare-companies/),
    [Manatt](https://www.manatt.com/insights/newsletters/health-highlights/the-tcpa-and-healthcare-consent-exemptions-and-ri))
- **Conclusion (per campaign).**
  - **Confirmation / Reminder** → meet all three factors; consent satisfied by the patient
    providing their number. Opt-out is fine. **No change needed.**
  - **Overdue Recall** → qualifies **only if strictly clinical**; any promotional framing makes
    it telemarketing → **prior express *written* consent** required (see Finding 2).
  - **Sales Qualification** → new leads have **no established treatment relationship** → fails
    factor (b) → **prior express written consent required**; opt-out is not a lawful basis.
  - The **AI voicemail** the agent leaves is itself an artificial-voice message subject to the
    same rules.
- **Recommendation.** Have healthcare-TCPA counsel classify each campaign. Add **express written
  consent capture at intake** for the marketing-basis campaigns, and **in-call identity
  disclosure + opt-out** (§7 omits both). Design-freeze-blocking for Recall and Sales
  Qualification.

### 2. The product design actively pushes Recall toward *exemption-voiding* content (and enables PHI-in-SMS)

- **Concern.** Recall's legal safety depends on the message staying **strictly clinical**, yet
  the platform lets **non-technical clinic admins author their own copy** (§5.2/§9.2), and Goal
  #2 (§2) is to **"recover revenue by re-engaging"** — which invites promotional wording. The
  same free-text authoring lets an admin place **clinical detail (PHI) into an SMS/email**, which
  are not secure channels.
- **Why it flagged.** The compliance layer as described (§9.1, Gap 5) checks for a *consent path*
  and *quiet hours* — but nothing checks whether the **content class** matches the campaign's
  legal basis, nor guards PHI in message bodies. The product's incentives and the law point in
  opposite directions.
- **Investigation & validation.** Confirmed the exemption is **content-conditioned**: "for dental
  recall reactivations to qualify… they must be strictly clinical reminders without promotional
  content," and "all telemarketing or promotional calls, even if health-related, require prior
  express written consent."
  ([Bass Berry](https://www.bassberry.com/news/tcpa-exemptions-for-healthcare-companies/),
  [Providertech](https://www.providertech.com/rules-for-tcpa-compliance-and-how-they-apply-to-health-care/))
- **Conclusion.** A **systemic** design gap: a clinic can silently convert an exempt recall into
  unlawful telemarketing — or leak PHI — just by editing a template, with no consent basis to
  fall back on and no validator to catch it. Invisible at authoring time.
- **Recommendation.** The compliance validator (Gap 5) must enforce a **content class per
  campaign** — block promotional language/offers in exemption-basis campaigns, require
  written-consent gating for promotional content, and add **PHI-term detection** on message
  bodies. Name these as concrete, testable validator requirements.

### 3. The healthcare-exemption frequency cap (≤3/week/provider) is a condition of legality, not a deferrable nicety

- **Concern.** Gap 14 defers global frequency capping / cross-campaign mutual exclusion, mitigated
  only "operationally by enabling few campaigns initially."
- **Why it flagged.** The exemption research surfaced explicit numeric caps. If those caps are
  what *earn* the exemption, deferring the capping mechanism means the low-risk campaigns can
  silently fall out of compliance.
- **Investigation & validation.** Confirmed: to stay within the healthcare exemption a provider
  may send **≤1 message/day and ≤3 combined calls+texts/week per patient**, each concise
  (≤1 min call / ≤160-char SMS), with an easy opt-out.
  ([Bass Berry](https://www.bassberry.com/news/tcpa-exemptions-for-healthcare-companies/),
  [goICON](https://goicon.com/blog/guide-to-tcpa-regulation-for-healthcare-providers-for-text-message-sms-phone-call-and-email-communication/))
- **Conclusion.** A patient matching Confirmation **and** Reminder **and** Recall in one week can
  exceed 3 messages — at which point the clinic **loses the exemption** and needs consent it never
  collected. The deferred control is exactly what keeps the exempt campaigns legal — a materially
  stronger conclusion than "over-contact is a UX risk."
- **Recommendation.** Promote a **per-patient, per-provider frequency cap (≤3/week, ≤1/day)** plus
  the ≤160-char / ≤1-min limits from "deferred" to a **launch compliance control**.

## Integration & vendor reality

### 4. NexHealth webhook subscriptions are per-subdomain → a per-location subscription lifecycle at scale

- **Concern.** The read model (§3.5) subscribes to NexHealth appointment webhooks for "thousands
  of clinics," but never states whether that's one subscription or one-per-tenant, nor who
  maintains them.
- **Why it flagged.** The entire trigger-discovery mechanism rests on webhook delivery; if
  subscriptions are per-location the operational surface is far larger than the scope implies.
- **Investigation & validation.** NexHealth docs: subscriptions are "a dependent sub-resource to
  the endpoint," and creating one **"requires a subdomain to scope which institution's events you
  are subscribing to"**; API access is **per location**. **No documented cap** on
  endpoints/subscriptions.
  ([webhooks](https://docs.nexhealth.com/docs/webhooks),
  [webhook subscriptions](https://docs.nexhealth.com/reference/webhook-subscriptions))
- **Conclusion.** At scale the platform must **programmatically create and maintain one
  subscription per onboarded location**, monitor each for NexHealth's ~48h-outage deactivation,
  and **re-subscribe + backfill per location**. That's a real per-tenant lifecycle the scope
  treats as a single "subscribe" step. The absence of a documented cap is an **unconfirmed scale
  assumption**.
- **Recommendation.** Scope the per-location subscription lifecycle explicitly (provision,
  monitor, auto-recover). **Confirm with NexHealth** (developers@nexhealth.com) whether there is
  a ceiling on subscriptions/endpoints per partner key before committing to per-location fan-out.

> **In plain English.** A webhook is NexHealth's way of *telling us* when something happens
> instead of us constantly asking — like a doorbell that rings us when an appointment is
> created, updated, or cancelled, so the engine knows who to enroll. The catch: NexHealth makes
> you subscribe to those alerts **one clinic at a time** (per subdomain). You can't say "tell me
> about everyone" — you say "tell me about Bright Smile Dental," then separately "Maple Dental,"
> and so on.
>
> *Example.* Onboard 500 clinics and you don't flip one switch — you create **500 separate
> subscriptions**. When clinic #501 signs up, you create another (and first pull its existing
> appointments, since webhooks only report *future* changes). And NexHealth **switches a
> subscription off** if our server is unreachable for ~48h, so for every clinic we must check the
> subscription is still alive and, if not, turn it back on and re-sync what we missed.
>
> *Is it an issue, a limitation, or just how it works?* Mostly **just how NexHealth works** — but
> it's a **real system we have to build**, not the one-time "we subscribe to webhooks" step the
> scope implies. If we skip it, the failure is silent: a clinic's subscription quietly dies, we
> stop hearing about their appointments, and their patients simply **stop getting reminders**
> with no error anywhere. The one genuine unknown is whether NexHealth caps how many
> subscriptions one partner can hold — hence the recommendation to confirm in writing.

### 5. Factual error: Retell dials Canada natively; BYO-SIP is *not* required for Canadian outbound

- **Concern.** §3.5 and §7 state "Retell-provisioned numbers dial US destinations only, so
  imported/BYO numbers carry Canadian outbound." Part I Gap 9 repeated this as fact.
- **Why it flagged.** The claim is used to justify part of the per-clinic **BYO-SIP** telephony
  architecture — a load-bearing assumption worth verifying rather than inheriting.
- **Investigation & validation.** Retell docs: Retell sells **native US *and* Canada** numbers and
  dials Canada directly; BYO/SIP import is needed only for destinations outside Retell's supported
  set or to use one's own telephony.
  ([purchase number](https://docs.retellai.com/deploy/purchase-number),
  [custom telephony](https://docs.retellai.com/deploy/custom-telephony))
- **Conclusion.** The stated rationale for Canadian BYO-SIP is **false**. For US + Canada,
  Retell-managed numbers suffice; BYO-SIP becomes a *preference* (own telephony / bind Twilio
  sub-account numbers), not a Canadian *necessity*.
- **Recommendation.** Correct the scope and Part I Gap 9. Re-evaluate whether per-clinic BYO-SIP
  is a v1 requirement or an optional path — this simplifies Canadian go-live.

### 6. Per-clinic cold email domains spam-folder reminders on day one (silent business failure)

- **Concern.** §3.5 mandates a per-clinic sending domain, and §1 promises "production-grade from
  day one." Gap 9 mentions "reputation warm-up" only in passing.
- **Why it flagged.** A brand-new domain has no sending reputation; blasting a recall list from it
  on day one is a classic deliverability failure — and for reminders, spam-foldering is a *silent*
  failure that still reads as "delivered."
- **Investigation & validation.** Resend guidance: warm-up concerns **domain** reputation on
  shared IP pools; a new-domain ramp runs Day 1: 150 → Day 7: 2,000, extending to ~42 days for
  higher targets; realistic reliable-bulk readiness is **2–4 weeks**; day-one cold blasts risk
  greylisting/spam-foldering/blocking. Gmail/Yahoo (enforced since Feb 2024) require SPF+DKIM+**DMARC
  alignment** and spam rate <0.3%.
  ([Resend warm-up](https://resend.com/docs/knowledge-base/warming-up),
  [dedicated IP](https://resend.com/blog/do-you-need-a-dedicated-ip))
- **Conclusion.** A clinic that onboards and immediately runs an email recall to hundreds of
  cold-domain patients lands largely in spam — reducing no-shows by ~0 while appearing to work.
  Undercuts Goals #1–#2 and contradicts "production-grade day one."
- **Recommendation.** Gate bulk email behind a **per-domain warm-up state** (or start on a warmed
  shared/subdomain and graduate), add deliverability monitoring (bounce <4%, spam <0.1%), and make
  **DMARC setup** an explicit onboarding step (Resend handles SPF/DKIM only).

> **In plain English.** Gmail, Outlook, and Yahoo decide inbox-vs-spam based on the **reputation**
> of the sending domain, and a brand-new domain has **no reputation** — it's a stranger. When a
> stranger suddenly sends a big burst of email, spam filters read that as spammer behavior and
> quietly route it to the spam folder. It's like **credit history**: a new domain has no credit
> score, so asking to "send 600 emails on day one" is like asking for a big loan with no history —
> you get declined. You earn trust by "warming up" — sending small volumes first and ramping over
> **~2–4 weeks**.
>
> *Example.* A clinic signs up Monday and immediately emails a **Recall campaign to 600 lapsed
> patients** from its new domain. Most land in **spam** — the patients never see them. The nasty
> part: our dashboard shows "600 delivered ✅" (Resend *did* send them), so it **looks** like it
> worked while re-bookings are near zero. A **silent failure** — no error, just no results.
>
> *Is it an issue, a limitation, or just how it works?* It's **how email works everywhere** (not a
> Resend or NexHealth flaw) — but it collides with the scope's "production-grade from day one"
> promise, because day one is exactly when a new domain is *least* trusted. The fix: don't let a
> clinic blast a big list from a cold domain — warm it up first, or start them on an
> already-trusted shared domain and move them to their own once it's warmed.

### 7. Voice concurrency is a hard throughput ceiling and a per-workspace cost multiplier

- **Concern.** §7.2 frames per-clinic concurrency limits as "match front-desk capacity" — as if
  concurrency is a comfort setting, not a hard constraint.
- **Why it flagged.** Recall/Sales campaigns imply calling large lists; Retell concurrency and its
  cost model determine whether that's feasible in a morning window.
- **Investigation & validation.** Retell: default **20 concurrent calls, per workspace**; raising
  it costs **$8/concurrency/month** (reserved) or **+$0.10/min** burst; concurrency is
  **per-workspace**, so workspace-per-clinic gives each its own 20-call pool. 800 calls at default
  20 ≈ **2–3.3 hours of saturated dialing** (worse with ring/no-answer/voicemail/retries).
  ([concurrency](https://docs.retellai.com/deploy/concurrency),
  [pricing](https://www.retellai.com/pricing))
- **Conclusion.** Concurrency is both a **throughput ceiling** and a **per-tenant cost line**;
  the scope's framing understates both.
- **Recommendation.** Decide workspace-per-clinic vs. workspace-per-DSO (shared pool = cheaper,
  tenants contend), model the concurrency/cost curve per clinic tier, and set default channel
  ordering so voice isn't primary for large lists. Interacts with Gap 8 pacing — the same 9 AM
  burst hits Retell concurrency *and* the NexHealth budget (see Finding 13).

## Engine logic & operational safety

### 8. The "already-solid" idempotency key is call-scoped and does not cover scheduled-step dispatch

- **Concern.** §12 and Part I list "idempotent dispatch" among **reused/solid** foundations,
  citing `(call_id, function_name, HMAC(args))`.
- **Why it flagged.** That key requires a `call_id`. A scheduled SMS/email/timer step firing days
  later — the new engine's core behavior — has no call in progress.
- **Investigation & validation.** Reasoned against the documented key structure and the scope's
  own "exactly-one-action across restarts" requirement (§5.4). The existing key is inherently
  per-live-call; scheduled dispatch needs a durable key like `(sequence_run_id, step_id, attempt)`
  enforced by the (not-yet-existing) durable scheduler.
- **Conclusion.** The reused mechanism does **not** apply to the riskiest new path (double-
  contacting after a worker restart). "Idempotent dispatch = already solid" is overstated.
- **Recommendation.** Specify the **step-dispatch idempotency key + backing store** as part of the
  durable-scheduler build (Gap 4); stop counting it as reused.

### 9. Version-pinning has no compliance kill-switch for in-flight runs

- **Concern.** §5.2: "in-flight runs continue on the version they enrolled under." Pause halts
  *new* enrollments only.
- **Why it flagged.** If a published workflow is later found non-compliant (missing consent path,
  unlawful content per Findings 1–2), existing runs keep executing the defective version.
- **Investigation & validation.** Cross-read §5.2 (versioning) against §5.1/§11 (compliance-first):
  no mechanism to force-stop in-flight runs on a specific version; the only lever is pausing new
  enrollments.
- **Conclusion.** A discovered legal defect cannot be stopped mid-flight. For a compliance system,
  "let the bad version drain" is unacceptable.
- **Recommendation.** Add an **emergency halt** that terminates all in-flight runs on a given
  workflow version, distinct from normal pause, surfaced in operator tooling (§9.3).

### 10. No spend / blast-radius controls on a money-spending, patient-contacting democratized engine

- **Concern.** "Configuration, not code" (§5.1) lets a non-technical admin publish a workflow that
  auto-dials thousands and spends real Retell/Twilio money; §12 lists budget controls as
  **"optional."**
- **Why it flagged.** "Democratized authoring" + "spends money + contacts patients under legal
  constraints" + "budget caps optional" is a recipe for a single misconfiguration causing a large,
  expensive, non-compliant outreach event.
- **Investigation & validation.** Cross-read §5.1–§5.3, §12, §7.2 — no enrollment ceiling, spend
  cap, or publish-time blast-radius check is specified.
- **Conclusion.** The safety net for the most damaging failure mode (mass mis-contact / runaway
  spend) is "optional, later." Given Findings 1–3, a bad config isn't just expensive — it can be
  unlawful at scale.
- **Recommendation.** Make **publish-time blast-radius warnings, per-workflow enrollment/spend
  caps, and step-up approval for large campaigns** launch requirements, not optional.

### 11. The Jan-2026 toll-free BRN mandate (now passed) may already block registrations

- **Concern.** The scope assumes toll-free verification is a smooth ~days-long onboarding step for
  Canada-first clinics.
- **Why it flagged.** Vendor compliance deadlines shift; a passed deadline can silently block a
  path the scope assumes is open.
- **Investigation & validation.** Twilio: as of **Jan 2026**, a valid **Business Registration
  Number** became **mandatory** for toll-free verification (US: EIN matching legal name; non-US:
  local registration number), and "2FA Required" brands must complete Authentication+. A2P 10DLC
  campaign approval is currently **elevated at ~10–15 days**.
  ([Twilio A2P/toll-free](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/onboarding-isv))
  *(Exact toll-free turnaround days surfaced in search but not verbatim in a fetchable doc page —
  verify in live Console.)*
- **Conclusion.** As of **2026-07-01** the mandate is in force. Any assumption that clinics can be
  verified without valid BRNs is stale; existing brands may already risk blocked traffic.
- **Recommendation.** Confirm current requirements in the live Twilio Console, make **BRN
  collection** a hard onboarding gate, and audit already-registered brands for BRN/Authentication+
  status.

---

## SECONDARY SET — additional meaningful observations

### 12. Clinic-level quiet hours are a TCPA liability for US cross-timezone clinics *(sharper than Gap 18)*

- **Concern.** §8 and Gap 18 settle on clinic-level timezone as "a reasonable launch posture."
- **Why it flagged / conclusion.** TCPA quiet hours (8am–9pm) follow the **called party's**
  timezone. A clinic in Eastern serving a Pacific patient, calling 9am ET = 6am PT = violation.
  Gap 18 frames per-patient TZ as a "refinement, not launch-blocking"; for US clinics with
  out-of-region patients this is a **compliance defect**, not a refinement — a materially different
  conclusion.
- **Recommendation.** For US clinics, widen quiet-hours to the intersection of plausible zones or
  require patient-timezone capture; treat per-patient TZ as launch-blocking *for US multi-zone
  clinics specifically.*

### 13. Send-time re-validation reintroduces per-contact NexHealth calls at the burst moment

- **Concern.** §3.5 leans on "re-validate live at send time" as the safety net for
  out-of-order/dropped webhooks.
- **Why it flagged / conclusion.** That means **every** confirmation/reminder makes a live
  NexHealth call before sending. 800 reminders = 800 validation calls against the 1,000/min key
  budget, at the exact 9 AM burst — partly offsetting the read model's "fewer calls than polling"
  benefit and compounding Gap 8's pacing problem.
- **Recommendation.** Validate **inside the paced send loop** (not all upfront); trust recent
  webhooks within a freshness window to skip redundant re-validation; quantify validation volume
  in the pacing design.

### 14. Cross-channel fallback (voicemail → SMS) crosses consent domains and assumes mobile

- **Concern.** §7.2 proposes "voicemail → fall back to SMS."
- **Why it flagged / conclusion.** Voice consent ≠ SMS consent, and the dialed number may be a
  landline (SMS fails silently). The fallback has hidden consent + line-type dependencies.
- **Recommendation.** Gate every channel switch through that channel's own consent + capability
  (line-type) check.

### 15. "Four launch campaigns" vs. deferred lead intake → effectively three

- **Concern.** §2, §10.4, §14 promise Sales Qualification as a live launch campaign, but its data
  foundation (lead intake, Gap 3) is now **Deferred**.
- **Conclusion.** The scope's headline "four campaigns" is inconsistent with the deferral —
  effectively **three** launch campaigns for net-new prospects.
- **Recommendation.** Reconcile the deliverable list with the Gap 3 deferral (note Sales
  Qualification is limited to existing-contact enrollment until lead intake is built).

### 16. "Production-grade day one, no hardening phase" vs. the net-new build size *(sharpens Gap 23)*

- **Concern.** §1 asserts everything ships production-grade with no demo/hardening phase; §3.4
  lists a vast net-new surface (engine, durable scheduler, DSL/validator, canvas UI, per-tenant
  infra, read model). Gap 23 notes the absence of rollout sequencing.
- **Conclusion.** "No phasing" is an unmanaged risk, not a strength; these systems can't all reach
  production grade simultaneously. This compounds the multi-week onboarding lead-times (Findings
  4, 6, 11).
- **Recommendation.** Replace "no phases" with an explicit delivery spine — **engine + durable
  scheduler → one channel (SMS) → one campaign (Reminder, lowest legal risk) → expand** — keeping
  "production-grade" as a per-increment quality bar.

---

## Sources (Part II)

- FCC — [AI voices are "artificial" under TCPA (Feb 8, 2024)](https://www.fcc.gov/document/fcc-confirms-tcpa-applies-ai-technologies-generate-human-voices) ·
  [FCC-24-17 full ruling](https://docs.fcc.gov/public/attachments/FCC-24-17A1.pdf)
- TCPA healthcare exemption — [Bass, Berry & Sims](https://www.bassberry.com/news/tcpa-exemptions-for-healthcare-companies/) ·
  [Manatt](https://www.manatt.com/insights/newsletters/health-highlights/the-tcpa-and-healthcare-consent-exemptions-and-ri) ·
  [Providertech](https://www.providertech.com/rules-for-tcpa-compliance-and-how-they-apply-to-health-care/) ·
  [goICON](https://goicon.com/blog/guide-to-tcpa-regulation-for-healthcare-providers-for-text-message-sms-phone-call-and-email-communication/)
- NexHealth — [webhooks](https://docs.nexhealth.com/docs/webhooks) ·
  [webhook subscriptions](https://docs.nexhealth.com/reference/webhook-subscriptions) ·
  [rate limiting](https://docs.nexhealth.com/reference/rate-limiting) ·
  [authentication](https://docs.nexhealth.com/reference/authentication-1)
- Retell — [concurrency](https://docs.retellai.com/deploy/concurrency) ·
  [pricing](https://www.retellai.com/pricing) ·
  [purchase number / geo](https://docs.retellai.com/deploy/purchase-number) ·
  [custom telephony](https://docs.retellai.com/deploy/custom-telephony)
- Resend — [domain warm-up](https://resend.com/docs/knowledge-base/warming-up) ·
  [dedicated IP guidance](https://resend.com/blog/do-you-need-a-dedicated-ip)
- Twilio — [A2P 10DLC / toll-free onboarding](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/onboarding-isv)

*All figures observed 2026-07-01. Unverified items (exact toll-free turnaround days; NexHealth
subscription cap per partner; Retell AMD accuracy claims) are flagged inline and require direct
vendor/legal confirmation before being treated as settled.*
