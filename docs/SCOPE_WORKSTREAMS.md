# Outstanding Scope — Workstream Breakdown

A cross-cut of [OUTSTANDING_SCOPE.md](OUTSTANDING_SCOPE.md), organised by **which system and
skill-set owns the work** rather than by the source document's Part numbering. Every one of the 47
items appears exactly once under a primary workstream, with cross-references where it spans more
than one.

Sizes are **build days at agent-driven pace** — writing, reviewing and testing, with parallel
agents — and are for sequencing only, not commitments. The build column is no longer the expensive
part of this backlog; the section *What doesn't compress* is where the schedule actually lives.

---

## Headline numbers

| | |
|---|---|
| Total items | **47** |
| Build effort | **≈ 63 days** at agent-driven pace — roughly 3 weeks across parallel lanes |
| Fully inside this repo (Platform + dashboard) | **28 items** |
| Fully outside this repo (Cloud Service / Connector) | **7 items** |
| Split across both | **12 items** |
| Blocked on a product decision | **1 item** (Item 11 on Decision B) |
| Gated on something other than code | **17 items** — see *What doesn't compress* |
| **Delivered so far** | **27 of 47**, plus Item 24 part-built — see *Delivered so far* |
| **Workstreams complete** | **WS2, WS6**; WS3 owes Item 11 and half of Item 15; WS7 owes Item 40; WS8 owes Item 37 |

---

## Delivered so far

| Item | Workstream | Commit | Notes |
|---|---|---|---|
| **36** · A screen for messages that could not be delivered | Dashboard UI & reporting | this change | Tenant- and location-scoped operator queue with the failure cause, redacted context and originating campaign run. Replay remains behind `write:replay`, is audited, advertises support per event type, and takes a row lock so concurrent clicks enqueue once. Dismissal requires a reason; optional free text is encrypted and excluded from audit metadata |
| **32** · Record who changed a campaign | Security, audit & RBAC | `7572ca4` | 15 `CAMPAIGN_*` action types, 18 endpoints decorated, static coverage test. Four of five acceptance criteria met in full; compliance-setting audit is reserved for Item 20 (no such endpoint exists yet) |
| **14** · Retry text messages | Campaign engine core | `7116457` | Found worse than documented: `SmsService` never raises, and the executor discarded its return value, so a Twilio rejection was recorded as a delivered contact. Three-way classification — the ambiguous network case is deliberately not retried, since Twilio's Create Message has no idempotency key |
| **15** · Delivery results into campaigns | Campaign engine core | `cc3f28a` | Step records the provider message id in `result_metadata`; terminal receipts mark `sent:delivered` / `sent:undelivered`. Branching on delivery failure deferred — the run has usually advanced past the step by then |
| **12** · Generate the three link types | Campaign engine core | `797063d` `e43378d` → `e73786f` `96d86c4` `bc70361` `04ec54a` `fd30337` `0dc95ed` `750ea50` | Signed, run-scoped, expiring tokens (action and expiry both inside the signature), the public landing endpoints, and the patient-facing slot picker: book, reschedule and cancel all finish unattended. Reschedule patches the original appointment rather than cancel-and-rebook; a slot lost mid-flow is told apart from a failed booking and re-offered in one round trip. **`750ea50` fixed the defect that made the rest unreachable** — `action_url` had always returned the API path, so `{{booking_link}}` resolved to the endpoint that hands the patient to staff rather than to the picker. Marked delivered once on tests that never checked where a link goes |
| **21** · Inbound enquiry store | Campaign engine core | `080e3a0` → `0196ba7` → `498ffba` → this change | The first implementation added `campaign_enquiries`; that was the wrong identity boundary. A lead is now a `Contact` with no `nexhealth_patient_id`, using the contact's institution RLS plus an idempotent `(institution_id, intake_key)`, email/phone hashes, attribution and encrypted notes. Intake matches key → either hash → the existing contact, because a "new lead" is often somebody the practice already knows. The UI now follows the model: two directories only — non-PMS **Contacts** and synchronized **Patients** — with old `/enquiries` bookmarks redirected. `campaign_enquiries` remains only for expand/contract safety and nothing writes it. Consent stays in `consent_records`, keyed to the same contact/identity gates |
| **30** · Block unsupported campaigns | Campaign engine core | `d3c444b` | Doc said flip warn→refuse; instantiation already refused. Real gap: requirements lived on template metadata and never reached the definition, so publish never re-checked. Now carried and re-evaluated; unknown still counts as unavailable |
| **13** · Enforce readiness at publish | Campaign engine core | `417bf54` | Doc was out of date — the publish path already ran the real readiness service, fail-closed. Real gap: SMS-not-provisioned was a *warning* while voice was an error, so a campaign published for a clinic with no Twilio sender and failed for every patient. Email's platform-address fallback stays a warning, since that mail does deliver |
| **16** · Cross-channel suppression | Campaign engine core | `46a3a9f` + `fca9d07` | Moved from "how the campaign was drawn" to an engine rule in the compliance gate, checked before quiet hours. Opt-out moved to the send node (`send_after_response`) after `fca9d07` — on `ComplianceMetadata` it was dead, since `publish_version` strips that block. Verified safe for both live campaigns — they hold only a voice attempt ladder, no post-response sends |
| **3** · Prevent the same booking being written twice | GoTracker booking safety | `e294420` | Connector asks the chart "did I already write this?" before every write, keyed on `CreatedUserId='ThirdPartyIntegrator'`. A retry returns the existing id and writes nothing. Root cause of the duplicate-booking class |
| **1** · Check the clinic's schedule before writing | GoTracker booking safety | `b066a8d` | Patient-resolves + slot-free re-checked on the same connection immediately before the write. Cancelled statuses release their slot; touching boundaries are not overlaps |
| **2** · A conflict outcome for writes | GoTracker booking safety | `4adc67c` | Third terminal status — never re-queued however many attempts remain. Own webhook action. Surfaced per-location on `/api/admin/sync_status` as `conflicts` / `failed` / `oldest_unwritten`. An admin can still re-queue deliberately |
| **4** · Report pending honestly | GoTracker booking safety | `0d096f2` + `af01334` | Platform half landed first, as the doc requires. Booking response and appointment reads carry `write_status` (`pending`/`written`/`failed`/`conflict`) + `foreign_id`, separate from `status`; PMS-origin rows read as `written`. Run-history visibility still rides with Item 11 |
| **5** · Recover in-flight writes after Connector restart | GoTracker booking safety | `305ed2d` | Item 3's read-back applied to patient creation too. Decision I is now recorded as the choice that shipped: rely on the write identifier and chart read-back, with no durable local patient-data store |
| **8** · Mapping review before live bookings | GoTracker operations & health | `a9fda29` | Writes that reach a patient are refused until a named person has reviewed the mapping. Reads and agent sync are deliberately not gated — a half-onboarded clinic can still be looked at and talked to, it just cannot have appointments written into it |
| **6** · Alert when a connection is unhealthy | GoTracker operations & health | `e178162` + `c1ed593` + `07afeb2` + `94d46e7` | Nine conditions evaluated every five minutes, collapsed into three CloudWatch alarms. Suppressed when the clinic is genuinely closed, so a practice with its lights off overnight does not page anyone |
| **7** · Complete the connection health screen | GoTracker operations & health | `8665f7b` | Five missing fields plus a findings panel, all over data that was already being collected. The conflict count it needed arrived with Item 2 |
| **10** · Operator runbooks for the GoTracker path | GoTracker operations & health | `724d982` | RB-1 to RB-5, each tied to an alarm from Item 6, plus a test that checks the runbooks against the source so they cannot quietly drift out of date |
| **9** · Sign the messages the Connector sends | GoTracker operations & health | `00699ca` + `f88aa62` | HMAC over the request body, with a three-mode transition and enforcement **shipping off** — turning it on before the fleet has updated would drop every clinic still sending unsigned |
| **39** · Privacy and audit review | Security, audit & RBAC | `a5d5ee4` + `13b48f5` (Cloud Service) | Three log sites were leaking patient records through raw exception interpolation — a NexHealth error quotes patient records, a Retell verification error quotes the transcript. 185 mutating endpoints classified; the six changing a location's operating hours had no record, and those decide when a patient may be contacted. Coverage is now enforced repo-wide rather than remembered |
| **34** · Record what caused each write | Security, audit & RBAC | `4b4f030` (Cloud Service) | `actor`, `trace_id` and `reason` on every queued write, carried into the Cloud Service's record. Run and step were already there; **actor** was the gap, since a patient acting on a campaign link carries a run id too. The Item 12 booking path wrote into a practice with no provenance row at all |
| **33** · Permissions for high-consequence actions | Security, audit & RBAC | Cloud Service audit fix | Four named permissions layered over the existing tenant check. Sync status was open to STAFF and returns patient names — narrowed and audited. Enforcement not built on the Cloud Service: one admin principal, so a permission would distinguish nobody; its admin actions were unlogged entirely and are audited there instead |
| **17** · Stop calling a service that is failing | Reliability & throughput | `cf65b51c` | Redis-backed breaker per service per clinic; every transition decided in Lua so racing workers cannot disagree. Half-open admits one probe held by a `SET NX` token with its own TTL, so a worker dying mid-probe costs one cooldown rather than wedging the breaker shut. Refused work is held on a timer, reusing the quiet-hours path — no run fails because a supplier had an outage. Only the caller decides what counts as a failure: a 4xx is a bad request, not a sick service |
| **18** · Limit how fast and how many messages and calls go out | Reliability & throughput | `30091b14` | A call slot is a **lease with an expiry**, not a counter — the doc's warning about a lost decrement is structurally impossible, since every acquire prunes what has lapsed. Slots are re-labelled to the provider's call id once placed, which is the only name the outcome handler has. Deliberately not released on the ambiguous timeout: the call may be live. Per-clinic ceiling (overridable on `Institution.outbound_call_limit`) plus per-provider send rates |
| **20** · Quiet-hours exceptions | Reliability & throughput | `9cc22fb` | One table for date, patient and message-class exceptions; NULL means "applies regardless", most specific wins, weighted so a patient's own preference always outranks a clinic rule. An exception **replaces** the day's window rather than intersecting it, which is what lets a 7am reminder go out before the doors open. Save-time validation runs the real evaluator rather than re-deriving the rule, so the check cannot drift from what the engine does. Creates the compliance-settings endpoint Item 32's audit was reserved for |
| **19** · Voicemail handling options | Reliability & throughput | `9cc22fb` | Two settings plus **two separate counters** — a counted-attempt allowance and a hard dial cap. The cap is what makes "voicemail does not consume an attempt" safe; without it that setting means unlimited. A claim later marked FAILED is neither a dial nor an attempt, so vendor 5xx errors cannot burn a patient's allowance without the phone ringing. Defaults preserve today's behaviour exactly |
| **35** · Alert on campaign engine problems | Reliability & throughput | `9cc22fb` | Seven alarms on the existing channel, plus a `WorkflowUndeliverable` metric nothing published and a log filter on Item 17's cut-off line. Two thresholds are structural; five are sized for current volume with the derivation recorded, so they can be re-derived rather than re-guessed as the practice count grows. **`failed_steps` was cumulative and is now windowed to 24h** — counted for all time it only ever rises, so any threshold is crossed once and stays crossed |

---

## Fixes found along the way

Not scope items, but they blocked or silently defeated scope work.

| Fix | Commit | Why it mattered |
|---|---|---|
| Migration chain replayable on a fresh database | `fa27614` | `alembic upgrade head` could not build a database from scratch, so **no new environment could be stood up and the entire RLS tier was unrunnable** — which is where Item 44's isolation coverage has to live. Four guards; a fresh build now matches dev exactly (87 tables, 559 indexes, 95 policies) and the RLS suite passes 11/11. Models and schema were verified to agree exactly — this was never drift |
| Security middleware no longer downgrades `Referrer-Policy` | `527be8b` | It overwrote the link endpoints' `no-referrer` with `strict-origin-when-cross-origin`, which still sends the full URL — token included — same-origin. Item 12's Referer defence was **absent in the running app**; the endpoint tests mount a bare router and never saw the middleware |
| `channel` value the response table permits | `dc74076` | The link endpoints wrote `"link"`, which the CHECK constraint rejects. Every confirm and handoff would have failed on insert; mocked sessions never touched the constraint |
| Enquiry column types + duplicate index | `f358857` | `String(36)` ids against `uuid` targets, and a model declaring two indexes of one name |
| Cross-channel suppression opt-out moved to the send node | `fca9d07` | On `ComplianceMetadata` it was dead code — `publish_version` strips that block, so the flag could never be switched on |
| Action links pointed at the API, never at the pages | `750ea50` | `action_url` returned `/api/campaigns/link/{action}` for every action, including after the patient-facing pages were built. `{{booking_link}}` therefore resolved to the handler that hands the patient to **staff** — the precise outcome the slot picker exists to avoid. Item 12 had been marked delivered on tests that never asserted where a link goes |
| The two new node types were unreachable by the engine | `02a3f1a` | `booking_link` and `patient_registration` were never added to `NODE_CAPABILITIES`, which the dispatcher consults before executing a step and the builder palette filters against. Both were invisible in the palette **and** would have failed their run with "not supported by this engine" — schema, executor, API enforcement and 71 tests, all correct and all unreachable. The guard added with the fix derives from the schema union rather than a hand-kept list, so the same omission cannot recur |
| Every environment minted links pointing at production | `77d637d` | `public_base_url` was a hardcoded default in `config.py` and nothing set it per environment, so staging generated patient links aimed at `app.scalenexus.ai`. Publish validation could not catch it: it refuses an *empty* base URL, not a wrong one |
| A fragile emergency-halt test, unmasked | `02a3f1a` | Pinned `session.execute` to a two-item `side_effect`, so it broke when `cancel_run` gained its Retell-SMS cleanup. Already failing in isolation on staging; full-suite ordering was hiding it |
| Clinic operators could never see SMS dead letters | this change | `send_sms_message` captured only `location_id`, while the `dead_letter_events` user RLS policy requires `institution_id` before it considers location. The new screen would therefore have looked complete while silently omitting failed texts. Capture now resolves the institution from the location before inserting |
| Person screens contradicted the identity model | this change | The dashboard exposed Enquiries and Patients as separate person concepts even though a lead and patient are lifecycle projections of one `Contact`. Worse, Patients listed callers and was shown only to clinics with **no** PMS, while PMS patients had no directory; location admins could open Enquiries but its API always returned 403. The UI now has Contacts (non-PMS people) and Patients (NexHealth/GoTracker-linked people), and manual contact creation writes the location visibility grant the backend enforces |

---

## Added beyond the scope

Not in the 47 items. Each came out of a question the scope did not ask.

| Addition | Commit | Why |
|---|---|---|
| A patient identity gate in front of action links | `bdb5c25` + `3c1a851` | A link binds a *run*, and the run names a contact — which is not the same as knowing who is holding the phone. A number reaches a household (the contact model says so outright), and one given to a clinic 18 months ago may have been reassigned. Opening a cancel link used to hand the appointment's time, provider and reason to whoever opened it, then let them cancel it. Reuses the voice agent's `_identity_gate_passes` rather than growing a second matcher, keeps its one-neutral-answer property so the page cannot be used to test guesses, and caps attempts — a phone call has natural friction, a web form has none. Running out fetches a human instead of showing a wall. The campaign author chooses when it applies; runs already in flight are exempt |
| Booking Link and Register Patient as configurable steps | `3f72962` + `c15d7eb` | The link was a bare merge field: the API offered every appointment type the practice software returned. The voice agent restricts what a new patient may book, but that rule lives in its Retell prompt — guidance an LLM follows, not a constraint the platform applies — so a patient following a link could pick something the phone agent would never have offered. Now a step with rules the server enforces, configured from the cached PMS lists rather than typed ids: NexHealth types `provider_id` as an integer, so a typed name made every registration fail as an opaque 503 |
| Enquiry intake credentials, issued by the clinic | `db98a99` | The intake surface existed but no external form could authenticate to it. One credential per form, so a practice can run a website form, a Typeform page and a paid-ads form at once and retire one without the others. Shown once, stored hashed, rotatable; intake now writes the matched/new `Contact`, never `campaign_enquiries` |
| A confirmation email after a link booking | `2f39b99` | Reuses the voice agent's template and its activation gate, so a clinic edits that wording once and both channels follow. Reads the address from the PMS rather than the page — a forwarded link must not redirect someone else's confirmation |

---

## Three codebases, not one

This is the first thing to get straight, because roughly a fifth of the backlog cannot be written on
this branch at all.

| System | Where the code lives | Items |
|---|---|---|
| **The Platform** — campaign engine, NexHealth integration, dashboard API | `nex_health/src/app` (this repo) | 28 fully, 12 partly |
| **The Dashboard** — clinic-facing React app | `nex_health/nexus-dashboard-web` (this repo) | ~14 items have a UI slice |
| **Cloud Service + Connector** — GoTracker path | **Separate codebase.** Only specs and CDK mirror locally at `../gotracker_synchronizer` (see `GoTrackerSync_Salman_Completion_Plan.md`) | 7 fully, 12 partly |

---

# WS1 · GoTracker booking safety
**5 items · ≈ 5 days · Cloud Service + Connector + Platform · TIER 0**

Was the highest-priority group in the whole backlog, and the only one where the software could
cause real-world harm with no error to warn anyone: double-booked slots in a live practice, and
patients told "confirmed" for appointments the practice will never see. Shipped as backend
`20260831-af01334` on staging and production, agent `2.1.8` (staging 100%, production registered at
0%); 386 backend and 157 agent tests at the close.

| # | Item | Size | Owner | Notes |
|---|---|---|---|---|
| **3** ✅ | Prevent the same booking being written twice — **done, `e294420`** | 1d | Connector + Cloud Service | Doc specified a deterministic key derived from booking content. What shipped reads back instead: the Connector asks the chart "did I already write this?", keyed on `CreatedUserId='ThirdPartyIntegrator'`, so a retry returns the existing id and writes nothing |
| **1** ✅ | Check the clinic's schedule before writing — **done, `b066a8d`** | 1.5d | Connector | Patient-resolves + slot-free re-checked on the same connection immediately before the write. Cancelled statuses release their slot; touching boundaries are not overlaps |
| **2** ✅ | A conflict outcome for writes that must not proceed — **done, `4adc67c`** | 1.5d | Cloud Service + Ops UI + **Platform mirror** | Terminal, never re-queued however many attempts remain, with its own webhook action. Surfaced per-location on `/api/admin/sync_status` as `conflicts` / `failed` / `oldest_unwritten`; an admin can still re-queue deliberately |
| **4** ✅ | Tell the caller when a booking is not yet real — **done, `0d096f2` + `af01334`** | 0.5d | Cloud Service API + Platform | Ours landed first, as the doc requires. Booking response and appointment reads now carry `write_status` (`pending`/`written`/`failed`/`conflict`) + `foreign_id`, separate from `status`. PMS-origin rows read as `written`. **Remaining: run-history visibility, which arrives with Item 11** |
| **5** ✅ | Recover in-flight writes after Connector restart — **done, `305ed2d`** | 0.5d | Connector | Doc expected a durable local record and Decision I to settle it. Items 3 and 5 converged on one mechanism instead — read back from the chart — so there is no local state, nothing to encrypt, and **Decision I never needed answering** |

**What this closes and what it does not.** The double-booking and false-confirmation classes are
both gone. Two things ride on elsewhere: **run-history visibility** for `pending` / `conflict`
arrives with Item 11 in WS3, and **proof** — Items 41 and 42 are still the only end-to-end evidence
the write path holds against a seeded practice database, and that sandbox does not exist yet. The
protections ship with their own coverage; the write-path suite remains outstanding. Item 7 in WS2
is now fully unblocked, since the conflict count it was waiting on shipped with Item 2.

---

# WS2 · GoTracker operations & health — ✅ COMPLETE
**5 items · ≈ 4.5 days · mostly Cloud Service / Ops UI**

The GoTracker path was already in good shape; this is the layer that lets an operator find out
something is wrong before the clinic phones in. It matters more since WS1 closed, because Item 2
introduced a terminal `conflict` state that someone has to notice and act on.

Shipped as backend `20260831-94d46e7` on staging and production. Test counts at the close:
**514 backend** (up from 386 when WS1 closed) and **157 agent**.

| # | Item | Status | Commit | What shipped |
|---|---|---|---|---|
| **8** | Mapping review before a clinic can take live bookings | ✅ | `a9fda29` | Writes that reach a patient are refused until a named person has reviewed the mapping. Reads and agent sync are deliberately not gated — a half-onboarded clinic can still be looked at and talked to, it just cannot have appointments written into it |
| **6** | Alert when a clinic's connection is unhealthy | ✅ | `e178162` + `c1ed593` + `07afeb2` + `94d46e7` | Nine conditions every five minutes, collapsed into three CloudWatch alarms. Suppressed when the clinic is genuinely closed, so a practice with its lights off overnight does not page anyone |
| **7** | Complete the connection health screen | ✅ | `8665f7b` | Five missing fields plus a findings panel, all over data that was already being collected. The conflict count it needed arrived with Item 2 |
| **10** | Operator runbooks for the GoTracker path | ✅ | `724d982` | RB-1 to RB-5, each tied to an alarm from Item 6, plus a test that checks them against the source so they cannot quietly drift out of date |
| **9** | Sign the messages the Connector sends | ✅ | `00699ca` + `f88aa62` | HMAC over the request body, three-mode transition, enforcement ships off |

---

# WS3 · Campaign engine core
**8 items · ≈ 9 days · Platform backend (+ builder UI) · TIER 1**

**Remaining: Item 11, and the branching half of Item 15.** Everything else here is delivered.

Where the silent failures live. Everything here is in this repo.

| # | Item | Size | Notes |
|---|---|---|---|
| **11** | A booking step inside campaigns | 3d | Hard blocker on Recall and Sales Qualification. Booking already works for the voice agent — it just isn't a campaign step. Needs booked / could-not-book / **pending** branches. Blocked on Decision B |
| **12** ✅ | Generate the three link types — **done, `797063d` `e43378d` → `e73786f` `96d86c4` `bc70361` `04ec54a` `fd30337` `0dc95ed`** | 3d | Signed run-scoped expiring tokens, three public endpoints, and the patient-facing slot picker: book, reschedule and cancel all finish unattended instead of raising a staff handoff. Reschedule patches the original appointment rather than cancel-and-rebook; a slot lost to someone else mid-flow is told apart from a failed booking and re-offered in one round trip |
| **14** ✅ | Retry text messages — **done, `7116457`** | 0.5d | Email already does this correctly — copy it. Must ship *with* the provider idempotency key or retries become duplicates |
| **15** ◐ | Delivery results into campaigns — **done bar branching, `cc3f28a`** | 0.5d | Terminal receipts now mark the step `sent:delivered` / `sent:undelivered`, so reporting tells arrival from acceptance. **Remaining: letting a campaign branch on a hard delivery failure** — by the time a receipt lands the run has usually advanced past the step, so it needs run-state work |
| **16** ✅ | Cross-channel suppression — **done, `46a3a9f`** | 0.5d | Today this is a property of how two campaigns were drawn, not an engine guarantee |
| **21** ✅ | Inbound enquiry store — **done, `080e3a0`** | 0.5d | Blocks Item 24 entirely. Needs RLS isolation + idempotent intake key + encryption at the same standard as patient contacts |
| **13** ✅ | Enforce readiness at publish — **done, `417bf54`** | 0.5d | Doc was out of date: publish already ran the real check, fail-closed. The gap was SMS-not-provisioned being a *warning* while voice was an error |
| **30** ✅ | Block unsupported campaigns — **done, `d3c444b`** | 0.5d | Doc said flip warn→refuse; instantiation already refused. The gap was requirements living on template metadata and never reaching the definition, so publish never re-checked |

---

# WS4 · The four campaigns
**3 items · ≈ 10 days · Platform + campaign design · TIER 2**

**Item 24 is part-built**: intake, patient conversion and the lead workspace are in; the trigger and
the campaign template are not.

Two campaigns are live and good. Two are two-step placeholders and one does not exist.

| # | Item | Size | Notes |
|---|---|---|---|
| **22** | Build out Appointment Reminder and Overdue Recall | 5d | Rebuild both to the depth of the live campaigns (~15–20 steps each). Reminder must re-check live practice data before every send. **Switching them on is the last action in this workstream.** Blocked on Decision D, needs Items 11, 12, 13, 25 |
| **24** ◐ | Build the Sales Qualification campaign — **intake and conversion done, `ecd1861` `db98a99` `a6e8932` `09e1986` `2f39b99`** | 4d | Was "does not exist in any form"; three of its five parts now do. **Intake**: a per-form credential a clinic issues itself, posted to by its own site or a hosted builder, with tolerant extraction of the answers array so a Typeform payload does not have to be reshaped first — plus staff entry by hand through the same path, so dedup and consent cannot diverge between the two. **Patient conversion**: the registration form and `create_patient`, which both adapters already implemented and only the voice agent could reach. **Working the lead**: a list, a stage derived from whether a practice-software record exists, and encrypted notes. **Remaining: the trigger and the template** — nothing enrols a landed lead into a campaign yet, and `BulkImportTrigger` is still a literal nothing implements. Decision C answered (signed webhook), and its open half answered too (staff may enter by hand) |
| **23** | Run Overdue Recall for GoTracker clinics | 1d | Must refuse to run on a clinic whose history sync is incomplete — otherwise it tells last month's patients they haven't been seen in two years |

---

# WS5 · NexHealth data families
**6 items · ≈ 11.5 days · Platform integration · TIER 2 — largest workstream**

Runs independently of WS3/WS4 and is the natural second developer's lane. Two of the six contracted
families are complete; four are not.

Every item here carries the same five-part definition of done: minimal field return, role-based
access control, audit on every access, per-workflow field allow-lists, and sandbox tests proving
sensitive fields are withheld. **No financial or clinical field reaches the voice agent without an
explicit per-workflow declaration.**

| # | Item | Size | Notes |
|---|---|---|---|
| **28** | Financials — charges, claims, payments, balances | 4d | **Largest data family in the scope.** Zero implementation today. Most sensitive data in the product — build the allow-listing *before* the retrieval |
| **25** ⏸ | Patient Communication — notes, documents, recalls — **DEFERRED 2026-08-30** | 3d | 1 of 7 record types built. **Highest-value item in Part 4** — Item 22's recall quality depends on it. Build this family first. Decisions F and G |
| **27** | Insurance — plans and coverage | 2d | Live hand-maintained data in production that the voice agent reads right now. **The migration must not lose it.** Decision H |
| **31** | Working Hours — reconcile the two sources | 1d | Detect divergence and surface it. Never auto-adopt either source — it would silently change what the agent offers on live clinics |
| **26** | Procedures — visit and treatment history | 1d | Currently a 5-entry extract on a legacy interface version. Unusable for recall targeting |
| **29** | NexHealth Operations — onboarding interfaces | 0.5d | The rest of this family is built. Shortens manual clinic onboarding |

---

# WS6 · Reliability & throughput — ✅ COMPLETE
**5 items · ≈ 5 days · Platform + shared state · TIER 3**

Nothing here is user-visible. All of it decides how the system behaves on a bad day — and
until this landed the engine had no self-protection at all: it could not tell that a dependency
was sick, could not pace itself, and could not tell anyone it was in trouble.

This was the only workstream in the backlog with no product decision, no cross-codebase
dependency and no missing environment. It was taken as a block for that reason, not because it
outranks Tier 2 — the contracted work is still more valuable, and still more blocked.

| # | Item | Size | Notes |
|---|---|---|---|
| **17** ✅ | Stop calling a service that is failing — **done, `cf65b51c`** | 1.5d | Per service per clinic, state in Redis, every transition decided in Lua. Fails open when the store is unreachable, reporting `UNAVAILABLE` rather than `CLOSED` so "healthy" and "unknown" stay apart. Held work is deferred on a timer, never failed |
| **18** ✅ | Limit how fast and how many messages and calls go out — **done, `30091b14`** | 1.5d | Doc warned that a missed decrement makes the count only ever rise until the clinic silently cannot call. Solved by construction: a slot is a lease that expires, so a lost release corrects itself one lease later rather than never. Concurrency is per institution, send rate per location — credentials are per location, capacity is not |
| **20** ✅ | Quiet-hours exceptions — **done, `9cc22fb`** | 1d | Date, patient and message-class exceptions, most specific winning. Rejected at save time by running the real evaluator, with the reason surfaced verbatim to the operator. Both load-bearing behaviours asserted, not assumed: held-until-morning, and no-window-means-blocked |
| **19** ✅ | Voicemail handling options — **done, `9cc22fb`** | 0.5d | Both settings, in the campaign builder and in the calling step. The separate dial cap is the part that matters: without it, "voicemail does not consume an attempt" means a voicemail-only number is dialled for ever |
| **35** ✅ | Alert on campaign engine problems — **done, `9cc22fb`** | 0.5d | Seven alarms on the existing email channel, over the figures that were already published and read by nobody. Also added the `WorkflowUndeliverable` metric, which did not exist, and windowed `failed_steps`, which was counted cumulatively so any threshold would be crossed once and stay crossed |

---

# WS7 · Security, audit & RBAC — ◐ 4 of 5 COMPLETE
**5 items · ≈ 5 days · Platform (+ all three systems for the review) · TIER 3**

Only Item 40 remains, and it cannot start: it is remediation of penetration-test findings, and
no test has been booked. Its size is unknown until one runs. The half of it that *can* be done
now — agreeing how findings are received, prioritised and owned across three codebases, before
the report lands rather than after — has not been done either.

| # | Item | Size | Notes |
|---|---|---|---|
| **40** | Respond to penetration-test findings | 2d | Unknown until the test runs. Agree intake and tracking **before** it starts |
| **32** ✅ | Record who changed a campaign — **done, `7572ca4`** | 1d | Was the only privileged area with zero audit coverage. 18 state-changing endpoints now audited with durable records; enrolment and template instantiation were gaps the doc did not name |
| **33** ✅ | Permissions for high-consequence actions — **done** | 0.5d | Four permissions with an explicit role map; STAFF hold none. Sync status narrowed to admins and audited. The Cloud Service audits its admin actions rather than gating them — one admin principal, so a permission would distinguish nobody. Revisit at a second admin |
| **34** ✅ | Record what caused each write — **done**, + `4b4f030` | 0.5d | `actor`, `trace_id` and `reason` on every queued write, carried into the Cloud Service's record and shown on the sync-status screen. Actor is the part a run id cannot answer: a patient acting on a campaign link carries one too |
| **39** ✅ | Privacy and audit review — **done**, + `a5d5ee4` `13b48f5` | 1d | Platform passes with 29 stated exceptions, none touching patient contact. Three log sites sanitised; audit coverage now enforced repo-wide. Cloud Service and Connector passes ran their side. [PRIVACY_AUDIT_REVIEW.md](PRIVACY_AUDIT_REVIEW.md) |

---

# WS8 · Dashboard UI & reporting — ◐ 1 of 2 COMPLETE
**2 primary items · ≈ 2.5 days · `nexus-dashboard-web` · TIER 3**

Only two items are *primarily* UI, but around fourteen have a dashboard slice.

| # | Item | Size | Notes |
|---|---|---|---|
| **37** | Outcome reporting — recalls booked, enquiries qualified, revenue | 1.5d | The three figures that answer "is this working?" Decision A defers the revenue figure; the first two still need Items 11 and 24 |
| **36** ✅ | A screen for messages that could not be delivered — **done, this change** | 1d | Platform and tenant operator views; RLS narrows clinic/location rows. Failure reason, redacted context and campaign run are visible. Retry is permission-gated, audited and row-locked against double clicks; dismissals record a bounded reason and an optional encrypted note |

**UI slices living inside other items:** campaign builder changes (11, 19, 22), publish-failure
surfacing (13), compliance settings (20), pre-launch checklist (8, 30, 31), run history (4, 15),
patient-facing link pages (12), permission-aware affordances (33), and the Cloud Service operator
screens (2, 7, 8).

---

# WS9 · Testing, CI/CD & documentation
**8 items · ≈ 10.5 days · all three systems · TIER 4**

| # | Item | Size | Notes |
|---|---|---|---|
| **41** | Test the practice-database write path properly | 3d | Sandbox practice DB + all twelve vendor write procedures + four failure modes. **This is the only proof that Items 1–3 actually work** |
| **46** | Automated build, migration checks and deployment | 2d | No pipeline in any of the three systems. Migration history has already forked more than once — that check belongs on the PR job. Connector installer needs code-signing |
| **42** | End-to-end test of the offline booking path | 1.5d | The product's most distinctive capability and highest-risk path, currently untested end to end |
| **38** | Disaster recovery procedures | 1d | In-progress campaign runs are the hard part, not the database — a restore must not re-contact patients or re-write appointments |
| **45** | Load testing | 1d | ⚠️ **Never run this locally** — use a throwaway prod-sized environment. Feeds the thresholds for Items 6 and 35 |
| **43** | Test each new data family against the sandbox | 1d | Part of each family's definition of done, not a follow-up |
| **47** | Guides for clinic administrators | 0.5d | Three guides, plain language. Connection-health guide depends on Item 7 |
| **44** | Prove clinic data isolation for the new data families | 0.5d | Extend the existing suite as each family lands |

---

# Sorted by size — build effort

Nothing in the backlog is longer than a week of building. Note how flat this ranking is: that
flatness is the point — the code stopped being the bottleneck, so the gates below decide the
schedule instead.

| Rank | # | Item | Days | Workstream |
|---|---|---|---|---|
| 1 | **22** | Reminder + Overdue Recall campaigns | 5 | Campaigns |
| 2 | **24** | Sales Qualification campaign | 4 | Campaigns |
| 3 | **28** | Financials data family | 4 | NexHealth |
| 4 | **25** | Patient Communication data family | 3 | NexHealth |
| 5 | **11** | Booking step inside campaigns | 3 | Engine |
| 6 | **12** | Booking / confirm / reschedule links | 3 | Engine |
| 7 | **41** | Practice-database write-path tests | 3 | Testing |
| 8 | **8** | Mapping review + go-live gate | 2 | GoTracker ops |
| 9 | **27** | Insurance data family | 2 | NexHealth |
| 10 | **46** | CI/CD across three systems | 2 | Delivery |
| 11 | **40** | Pentest remediation | ~2 | Security |
| 12 | **1** | Pre-write schedule check | 1.5 | GoTracker safety |
| 13 | **2** | Conflict outcome | 1.5 | GoTracker safety |
| 14 | **17** | Circuit breaker | 1.5 | Reliability |
| 15 | **18** | Concurrency + rate limits | 1.5 | Reliability |
| 16 | **37** | Outcome reporting | 1.5 | Dashboard |
| 17 | **42** | End-to-end offline booking test | 1.5 | Testing |

---

# What doesn't compress

Agents collapse the writing. They do nothing to the seventeen items below, which wait on a person,
an environment, or the calendar. Start these clocks now and the ~63 build-days fit underneath them;
leave them and they become the critical path.

| Gate | Items | Why it doesn't compress |
|---|---|---|
| **Nine product decisions** | 4, 5, 11, 22, 24, 25, 27, 37 | Decisions A–I; six need the client. **B is urgent** — it blocks Item 4 in the first stage and Item 11 in the third. Parallelism does not route around an unanswered question |
| **A practice-DB sandbox that doesn't exist** | 41, 42 — and 1, 3, 5 depend on it | No seeded GoTracker database to test writes against. The Tier 0 protections can be written but not proven, and proving them is the point |
| **Staging observation windows** | 6, 35 | Both specify thresholds from real observed values. The alarms are an afternoon; the numbers in them need days of staging traffic |
| **An unscheduled pentest** | 40 | Remediation cannot start before the test runs. Book it now and findings land while other lanes build |
| **Another team's codebase** | 19 items | Cloud Service / Connector work moves at their pace. Item 4 has a hard ordering constraint across the boundary — our side first |
| **Rollouts that must be watched** | 9, 22, 27, 38, 45 | Connector fleet update window, switching campaigns on for real clinics, migrating live insurance answers, a DR rehearsal, a throwaway prod-sized load environment. Elapsed time by nature |

---

# Sorted by importance — the tiers

**Tier 0 · Stops active harm.** Items **3, 1, 2, 4, 5**. The software can today double-book a live
clinic's schedule and tell a patient an appointment is confirmed when the practice will never see it.
Nothing else competes with this.

**Tier 1 · Silent failures and hard blockers.** Items **12, 32, 14, 15, 6, 35, 11, 30, 16, 13**.
Things that fail without telling anyone, plus the two blockers (11, 12) that half the remaining
feature work sits behind.

**Tier 2 · Contracted features not yet built.** Items **25, 26, 22, 23, 21, 24, 27, 28, 31, 29**.
Two campaigns and four NexHealth data families. The largest volume of work, and the most visible to
the client.

**Tier 3 · Operator tooling and resilience.** Items **7, 8, 36, 33, 34, 17, 18, 20, 19, 9, 37**.
Makes the product supportable and survivable rather than merely functional.

**Tier 4 · Proof, operations and documentation.** Items **41, 42, 45, 46, 10, 47, 38, 39, 40, 43,
44**. Note that 41 and 42 are the *only* evidence Tier 0 actually works — they are last in sequence,
not last in importance.

---

# Flags before we start

**1 · Decision B blocks Tier 0 and Tier 1.** When a GoTracker booking is accepted but not yet
written, does the campaign pause and wait, or exit and hand the patient to staff? It is a client
decision and it blocks Items 4 and 11 — one in the first stage, one in the third. Raise it now,
not when we reach it.

**2 · A fifth of the backlog is in someone else's codebase.** Items 1, 3, 5, 7, 9, 10 are pure
Cloud Service / Connector work, and 2, 4, 6, 8, 34, 42 are split. That coordination — particularly
the interface change in Item 4, where our side must land first — needs a named owner on both sides.

**3 · Fourteen items touch a dashboard that is being refactored right now.** Codex is mid-flight
replacing `components/ui/*` with `components/foundation/*`. Any new screen built against the old
primitives will conflict. Either sequence UI work behind that refactor or build against the new
foundation from the start.

**4 · Three items carry a migration or live-data hazard.** Item 27 (insurance answers the voice
agent reads today), Item 31 (silently changing offered slots on live clinics), and Item 22
(switching campaigns on before links and booking exist).

**5 · Item 45 must not run locally.** Load testing goes on a throwaway prod-sized environment.

---

# Suggested first moves on this branch

Everything below is Platform-side, in this repo, unblocked, and in dependency order.

1. **Item 4 — Platform half.** Handle `pending` as an outcome distinct from success and failure, and
   fix the message that always claims success. The doc explicitly requires our side to land before
   the Cloud Service starts sending the status. *(Confirm Decision B first — it shapes the campaign
   behaviour, though the plumbing can be built either way.)*
2. **Item 32 — campaign audit.** No dependencies, closes a genuine compliance gap, pure backend.
3. **Item 12 — link generation.** Currently unsafe, and blocks Items 13 and 22.
4. **Item 14 + 15 — text retry and delivery receipts.** Build 14 with the provider idempotency key
   in the same change; 15 is small and independent.
5. **Item 25 — Patient Communication family.** Start the second lane here if there is a second
   developer; it is the prerequisite for Item 22 being any good.
