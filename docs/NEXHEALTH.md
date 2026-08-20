# NexHealth Integration

Last reviewed: August 2026. Code lives in `src/app/nexhealth/` (transport: auth,
HTTP, rate limiting) and `src/app/pms/nexhealth/` (the adapter: domain calls and
response mapping). If you're debugging a booking or slot problem, start with the
[Caveats and edge cases](#caveats-and-edge-cases) section — most surprises are
already catalogued there with the workaround we ship.

NexHealth is a sync layer over dental PMSs (Dentrix, Eaglesoft, Open Dental, …).
We never talk to the PMS directly; NexHealth exposes patients, providers,
operatories, appointment types, availability, and bookings over one REST API and
writes back into the clinic's PMS. API docs: https://docs.nexhealth.com.

Not every underlying PMS supports every NexHealth endpoint — per-PMS capability
matrices are checked into
[`Supported_API_Per_PMS_Nexhealth/`](Supported_API_Per_PMS_Nexhealth/)
(one JSON per PMS). Check there first when a clinic on a particular PMS reports
a feature "not working".

## Account model

One platform-level NexHealth account/API key for all clinics. Per-clinic
isolation comes entirely from two values on each `InstitutionLocation`:

- `nexhealth_subdomain` — NexHealth's own tenant partition
- `nexhealth_location_id` — the location within that subdomain

The adapter factory fails closed if either is missing, and `location_id` is
mandatory on every PMS-touching route — for a multi-location institution there
is no "default" location, because guessing one would silently route bookings
into the wrong clinic's PMS (`src/app/pms/factory.py:91-99`).

`institutions.nexhealth_api_key_encrypted` exists for a future per-clinic
credential model but is not currently used by the adapter path.

## Auth and token lifecycle

`POST /authenticates` with the API key (bare key in `Authorization`, no
`Bearer` prefix) returns a token valid for exactly 3600s
(`src/app/nexhealth/auth.py`).

Token caching (`src/app/nexhealth/token_manager.py`):

- Redis-backed cache shared across all workers/tasks; in-memory fallback for
  single-process dev. TTL is `expires_in - 300` (5-minute safety margin),
  floored at 60s.
- Refresh is protected by an in-process `asyncio.Lock` plus a Redis
  `SET NX EX 30` distributed lock, so one worker fetches and the rest poll the
  cache (0.2s interval, 30s max). If the lock holder dies, pollers time out and
  fetch locally — a brief thundering herd, but requests still complete.
- All Redis failures are fail-open: cache miss → fetch; lock unreachable →
  proceed as if held. A Redis outage degrades to more auth calls, not an outage.

## Rate limiting

NexHealth's documented limits: 100 req/s global per key, 10 req/s for
`GET /appointments` and slot reads (`GET /appointment_slots` on legacy v2,
`GET /available_slots` on stable v3), 1000 req/min for patient/appointment
endpoints, 2000 req/min otherwise.

Since the whole fleet shares one key, limiting must be cluster-wide:
`src/app/nexhealth/rate_limit.py` classifies each request into an endpoint
class and atomically checks three Redis fixed-window counters (global/s,
class/s, class/min) in one Lua script. Notes:

- Keys use `SHA256(api_key)[:16]`, never the key itself.
- Fixed windows allow up to 2x burst at window boundaries; the reactive 429
  handler in the HTTP client is the backstop.
- Waiters add 10–80ms jitter so a blocked burst doesn't stampede the next window.
- Fail-open on Redis errors, same rationale as the token cache.

## HTTP client behavior

`src/app/nexhealth/http_client.py`: 30s timeout, keep-alive pool (10/20),
3 retries. 429s honor `Retry-After`; other failures use linear backoff.
A 2xx with `{"code": false}` in the body is still an error (NexHealth's
convention) and raises `NexHealthAPIError` with their `error` list.

Response bodies are never logged — NexHealth validation errors echo back
patient-submitted fields, so logs carry status + byte count + method + path only.

## Reference-data sync

`src/app/services/sync_service.py` pulls providers, appointment types,
operatories, and appointment descriptors into local
`institution_*` tables, keyed by `(institution_id, location_id, source_id)`.
Runs on demand (location setup flow / admin action), not on a schedule.
Upsert-only: rows deleted in NexHealth are not removed locally — staleness is
visible as `synced_at` age. Acceptable for reference data; don't rely on these
tables for anything booking-critical (slot search always hits the live API).
Operatories also carry a local `is_hidden` flag. PMS sync updates the room name
and active state but preserves that local visibility preference; hidden
operatories remain visible on the Operatories setup page and are filtered out of
appointment-type and scheduling selections.

## Slot search and booking

Raw bookable slots come from a contract-aware path: legacy v2 calls
`GET /appointment_slots`; stable v3 calls `GET /available_slots`. The request
parameters we use and the response shape are compatible, so the adapter still
flattens the nested per-location/provider response into universal slots. We
then filter locally
(`src/app/services/slot_filter.py`):

Before Retell returns slot results, blank provider names are enriched from the
location-scoped ScaleNexus `institution_providers` cache because NexHealth slot
groups reliably include provider IDs but not provider names.

1. Buffer: drop slots starting before `now + provider.buffer_minutes`.
2. Operating hours + breaks: per-day windows configured on the location,
   evaluated in the clinic's timezone.
3. Same-day cutoff: if the provider has *no* appointments booked today and the
   current time is past their cutoff, hide all remaining same-day slots (the
   provider probably isn't coming in). The "any appointments today?" check
   queries NexHealth live and **fails safe**: on any error it assumes
   appointments exist and leaves slots visible.

Booking is `POST /appointments` (body wrapped under `"appt"`), cancel is
`PATCH /appointments/{id}` with `cancelled: true`, and confirmation is
`PATCH /appointments/{id}` with `confirmed: true`. Before posting a booking,
the adapter re-queries the contract-aware slot endpoint for the selected day,
provider, appointment type, and operatory, then only proceeds when the selected
slot still matches exactly. The match checks start time and provider, checks
appointment type and operatory when supplied, and pins the end time from the
returned slot or from appointment-type duration when NexHealth omits `end_time`.

This validation is not a lock. Two agents can still race after validation; if
the booking POST loses that race, the adapter returns a controlled failure so
the caller can offer fresh slots instead of silently booking a different time.
Legacy reschedule still books the new slot first, then cancels the old one — if
the new booking fails the patient keeps their original appointment; if the
cancel fails after a successful booking we return success with a warning rather
than unwinding the new booking (`src/app/pms/nexhealth/adapter.py`).

`reschedule_appointment_v2` uses the same slot validation but, when the
configured contract is stable v3 and the underlying PMS is known to support
NexHealth appointment updates, it patches the existing appointment with
`start_time` and `end_time` instead of creating a second appointment. The direct
PATCH path is conservatively enabled for Dentrix, Dentrix Enterprise, Eaglesoft,
and Open Dental. Denticon appears in NexHealth's migration guide but not the
endpoint reference, so it is not enabled without explicit confirmation. All
other PMSes fall back to the legacy book-new-then-cancel-old flow.

Appointment list reads always choose cancellation semantics explicitly during
the v3 migration. Booking-critical reads such as the "has appointments today?"
slot cutoff check send `cancelled=false` and only consider active appointments.
Backfill/reconciliation scans fetch active and cancelled/deleted rows as two
separate filtered reads (`cancelled=false` then `cancelled=true`) so stable v3's
broader omitted-filter default cannot silently change workflow behavior.

## Caveats and edge cases

Everything below was discovered the hard way and is encoded in the adapter
with tests. File references point at the workaround.

**Phone numbers: 10-char truncation + NANP validation.** `POST /patients`
truncates `phone_number` to its first 10 characters at storage time, but
`GET /patients?phone_number=` does exact-string match — so create and lookup
must agree on the same normalized form. Worse, NexHealth validates the
truncated value against NANP rules (area code must start 2–9), so a US number
sent as `+15054821234` would truncate to `1505482123` and be rejected as
invalid. We pre-normalize: strip the leading `1` from E.164 NANP numbers, then
take 10 digits (`_normalize_phone_for_nexhealth`,
`src/app/pms/nexhealth/adapter.py:45-100`; tests in
`tests/unit/test_nexhealth_phone_normalization.py`).

**"Availabilities" are working windows, not bookable slots.** The stable
API's naming is misleading: an "availability" is a provider's recurring or
one-off *working window*; actual bookable slots are computed by NexHealth
(windows minus existing appointments) and come from the contract-aware slot
path: `/appointment_slots` on legacy v2 and `/available_slots` on stable v3.
Don't reach for `/availabilities` or `/working_hours` when you mean "what can
the patient book".

**Setup can bulk-link a date range of dated work windows.** The provider
scheduling page has a "Link date range" action that reads real NexHealth
working-window records, filters them to dated rows whose `specific_date` falls
inside the selected range for the selected provider and modal-selected visible
operatories, and PATCHes those records with the selected appointment types.
Hidden operatories are excluded from all-visible selections and rejected if
submitted explicitly. It deliberately does not patch recurring rows with only
`days`, because that would affect future weeks too, not just the selected range.

**`/availabilities` returns empty for PMS-synced schedules.** For providers
whose schedule syncs from the PMS, the endpoint can return 200 with zero rows
even though the provider has working hours. The same windows *are* embedded in
`GET /providers?include[]=availabilities`, so `list_availabilities()` merges
both sources by ID (`src/app/pms/nexhealth/adapter.py`). Without this, the setup
UI shows providers as having no hours.

**Working-window management must be enabled by NexHealth support.** Writing
working windows ("availabilities") for a clinic is not self-serve on the
stable API — it has to be enabled by NexHealth's team per practice, via a
support request. Factor the turnaround into clinic onboarding timelines; a
clinic whose window sync silently no-ops is usually one where this was never
enabled.

**`cancelled` vs `canceled`.** Appointment payloads use either spelling
depending on the path that produced them; we check both
(`adapter.py:300`). Similarly "already cancelled" errors on cancel are detected
by case-insensitive substring match and treated as success.

**Inconsistent response nesting and pagination.** `GET /patients/{id}` may put
the record under `data.user`, `data.patient`, or directly in `data` — we try all
three (`adapter.py`). Patient phone/DOB may be top-level or under `bio`
(`mappers.py`). Appointment-type duration arrives as `minutes` or `duration`.
List reads go through `src/app/nexhealth/pagination.py`: legacy v2 offset lists
can nest rows under a plural key such as `data.patients`, while stable v3 cursor
lists return rows directly under `data` and advance with
`page_info.end_cursor`. Writes must still wrap the body under the singular
resource name (`{"appointment_type": {...}}`) or you get `Missing parameter`
back.

Patient list projection no longer requires `location_ids`. Stable v3 omits that
field, so location-scoped backfills grant contact visibility from the
adapter-bound `InstitutionLocation` that produced the patient row. Legacy v2 rows
that still carry `location_ids` continue to resolve those locations first.

**Availability filtering is silent.** Windows with `active: false` and one-off
windows whose `specific_date` has passed are dropped during mapping with no
indication (`mappers.py:100-106`). `ignore_past_dates=True` is the default on
the list call. If a clinic asks "why doesn't this window show up", check these
first.

**Appointment pagination bounded at 10 pages.** The
has-appointments-today check scans at most 10×50 appointments for latency
reasons; a provider with >500 appointments in one day would be misread — and if
the payload shape is unexpected (occasionally `data` is not a list) we log and
assume appointments exist, because the failure mode of guessing wrong is hiding
bookable slots.

**Unconfigured operating hours mean no hour filtering.** If a location hasn't
configured operating hours, slot filtering applies only the buffer — slots
outside clinic hours will be offered to callers. Hours setup is part of
onboarding for a reason (`slot_filter.py:202-204`).

**Descriptor IDs are stringly typed.** EMR appointment-descriptor IDs are
numeric for some PMSs, alphanumeric for others; we coerce to `int` when
possible and pass strings through otherwise (`adapter.py:509-516`).

**Token/limit infrastructure is fail-open by design.** Both the token cache
and the rate limiter treat Redis errors as "proceed". The deliberate trade:
a Redis outage must not take down all PMS traffic; NexHealth's own 429s plus
the client retry are the real enforcement. If you see elevated 429s and
re-auth calls together, check Redis before checking NexHealth.

## Failure handling summary

| Failure | Behavior |
|---|---|
| NexHealth 429 | Sleep per `Retry-After`, retry up to 3x, then `NexHealthRateLimitError` |
| NexHealth 5xx / timeout | Linear-backoff retries, then error to caller |
| `{"code": false}` body | `NexHealthAPIError` with their error list (validation, conflicts) |
| Redis down | Token cache + limiter fail open; expect extra auth calls and some 429s |
| Booking race (slot taken) | Surfaces as a `code:false` validation error from NexHealth; agent offers another slot |
| Reschedule: new booking fails | Old appointment untouched, error returned |
| Reschedule: cancel-old fails | Success + warning; old appointment may need manual cleanup |

Tests that pin this behavior: `tests/unit/test_nexhealth_token_manager.py`,
`test_nexhealth_rate_limiter.py`, `test_nexhealth_phone_normalization.py`,
`test_nexhealth_pagination.py`, `test_nexhealth_adapter_appointments.py`,
`test_slot_filter.py`, and `tests/integration/test_slot_duration_edge_cases.py`.

## Post-visit completion (derived, not observed)

Post-visit campaigns — the shipped `post-op-followup-after-confirmation`
template — enrol when an appointment reaches a terminal visit state. On GoTracker
that state arrives for free: Chair Flow reports progress and transitions to
`Completed` when the patient leaves.

**NexHealth has no equivalent.** Its webhook vocabulary is
`appointment_insertion` / `appointment_created` / `appointment_updated` /
`appointment_cancelled` / `appointment_confirmed`, `patient_created` /
`patient_updated`, and `sync_status`. There is no checkout, no check-in and no
completion event, and the adapter exposes no such concept. So completion is
**derived**.

`sweep_nexhealth_completed_visits` (Celery beat, every 10 minutes) marks a
NexHealth appointment complete once:

- the institution's `pms_type` is `nexhealth` — GoTracker rows are never touched,
  they carry real Chair Flow data
- the appointment is still `scheduled`, i.e. not cancelled
- `start_time + duration` has passed, where duration comes from the matching
  `institution_appointment_types.duration_minutes` and falls back to
  `nexhealth_post_visit_default_duration_minutes` (60) when the type is unknown
- the visit ended within `nexhealth_post_visit_lookback_hours` (72)

It writes `flow_state = "Completed"` and `flow_changed_at = <computed visit end>`
onto the working-set row, then fires `trigger_appointment_state_workflows`.

Two details that matter:

- **`flow_changed_at` is the end of the visit, not sweep time.** The post-op
  template waits a fixed offset from that anchor, so it has to mean the same
  thing on both PMSs or NexHealth patients would be called late by up to the
  sweep interval.
- **The template is not modified.** The trigger matcher skips `status_ids` when
  empty and `confirmed`/`preconfirmed` when null, so a synthesized
  `flow_state="Completed"` satisfies the shipped definition as-is. One campaign
  definition, both PMSs.

Safety properties:

| Concern | Handling |
|---|---|
| Re-triggering the same visit | `flow_state` excludes the row next sweep, and `flow_changed_at` is folded into the enrollment idempotency key |
| Crash mid-sweep | Marks are committed before any trigger fires, so a crash re-marks rather than double-enrolling |
| First run on a busy clinic | The lookback bounds the reach; enrollment separately refuses work older than the trigger's `max_followup_delay_hours` |
| Cancelled visits | Excluded by the `status = 'scheduled'` predicate |

Known bound: the SQL pre-filters on `start_time` within the lookback, so an
unusually long appointment that *started* before the window but *ended* inside it
is missed. With a 72h window and typical durations this is not reachable in
practice.

**What this cannot do:** NexHealth exposes no no-show or completion status, so a
patient who never turned up is indistinguishable from one who was treated. Their
appointment is not cancelled, so the sweep marks it complete and the follow-up
campaign calls them. Suppressing that needs a real status signal from NexHealth.

## V3 webhook shadow validation

REST cutover and webhook cutover stay separate. Existing live subscriptions
continue to deliver v2-shaped payloads until they are replaced, while v3
validation traffic is sent to shadow-only endpoints:

- `POST /api/v1/nexhealth/webhooks/shadow/appointments`
- `POST /api/v1/nexhealth/webhooks/shadow/patients`
- `POST /api/v1/nexhealth/webhooks/shadow/sync-status`

These routes verify the NexHealth signature, capture the delivery, and return
2xx after capture even when JSON parsing fails. They do not write to the live
`nexhealth_webhook_events` ledger, enqueue workflows, update appointment or
patient projections, or affect live subscription health.

Shadow captures live in `nexhealth_webhook_shadow_events`. Raw payloads are
encrypted and kept under the same short NexHealth webhook raw-payload retention
window only after the delivery resolves to one institution. Unresolved or
ambiguous shadow deliveries keep parse/resolution status and a keyed payload hash
but do not retain raw payloads or extracted PMS resource identity. Redacted
payloads, API contract, event/resource metadata, provider delivery/subscription
ids when present, parse status, extracted business event identity, and
institution/location resolution results are stored for institution-scoped
validation. Shadow lifecycle rows live in
`nexhealth_webhook_shadow_subscriptions` and store the returned NexHealth
endpoint `secret_key` encrypted so each shadow endpoint can verify against its
own signing secret.

To create shadow lifecycle rows and optional provider subscriptions, set
`NEXHEALTH_SHADOW_WEBHOOK_CALLBACK_BASE_URL` to the public API origin and run the
manual Celery task
`src.app.tasks.automation_workflow.ensure_nexhealth_shadow_webhook_subscriptions`.
The task is intentionally not scheduled in Celery beat.

After shadow validation passes, v3 subscriptions can be pointed at the existing
live handlers (`/appointments`, `/patients`, and `/sync-status`). The live
`nexhealth_webhook_events` ledger deduplicates overlap by business event identity,
not provider delivery id or subscription id, because the same PMS change can be
delivered once by an old v2 subscription and once by a new v3 subscription.
The key shape is:

`Resource:pms_resource_id:event_family:change_marker`

Appointment changes use the NexHealth appointment id and markers such as
`start_time`, `updated_at`, or `cancelled:true`. Patient changes use the patient
id and `updated_at`/`last_sync_time`/`event_time`. Sync-status changes use the
subdomain plus resolved local locations and the read/write status timestamp. A
v3 patient payload with no `location_ids` can still update the institution-level
patient/contact projection when the subdomain resolves; location visibility is
only granted when explicit location ids are present or the subdomain maps to one
unambiguous local location.

## V3 cutover reporting

The migration runbook uses
`src.app.scripts.nexhealth_v3_cutover_report` as the repeatable baseline and
monitoring check. It is an ad-hoc read-only script, not a scheduled job. Save a
pre-cutover snapshot after appointment and patient backfills, then compare
post-cutover snapshots against it:

```bash
.venv/bin/python -m src.app.scripts.nexhealth_v3_cutover_report \
  --save-snapshot /tmp/nexhealth-v3-pre-rest.json

.venv/bin/python -m src.app.scripts.nexhealth_v3_cutover_report \
  --baseline /tmp/nexhealth-v3-pre-rest.json \
  --save-snapshot /tmp/nexhealth-v3-post-rest.json \
  --fail-on-rollback-signal
```

Run the report with `DATABASE_ADMIN_URL` set. It intentionally refuses to
bootstrap from the app `DATABASE_URL` because it reads cross-tenant operational
state.

The report reads the existing subscription lifecycle rows, appointment and
patient working sets, live webhook ledger, shadow webhook tables, sync-status
rows, and Retell audit failures for appointment writes, patient lookup, and slot
search. Its `assessment.rollback_recommended` flag is driven by count drops or
failure increases relative to the saved baseline. Its `assessment.cleanup_ready`
flag stays false until a baseline is supplied, the app is on `stable_v3`, the
stable window has elapsed, v2-pinned webhook overlap removal is confirmed, and
shadow/live failure signals are clean.

## API contract selection

NexHealth API versioning is selected by `NEXHEALTH_API_VERSION`, normalized at
startup into one internal contract target:

- `v2`, `v2.2.2`, and `legacy_v2` select the legacy v2.2.2 contract.
- `v3`, `v3.0.0`, `v20240412`, and `stable_v3` select the stable v3 contract.

Unknown values fail startup. Request headers are derived from that normalized
target, not hand-composed independently. Legacy v2 sends
`Nex-Api-Version: v2` with the legacy versioned `Accept` header. Stable v3 sends
`Nex-Api-Version: v3.0.0` with a non-versioned JSON `Accept` header.

The NexHealth adapter also derives renamed scheduling paths from the same
contract target: legacy v2 uses `/appointment_slots` and `/availabilities`;
stable v3 uses `/available_slots` and `/working_hours`, including the v3
`working_hour` body wrapper for working-window writes. This version-aware
routing is temporary migration scaffolding and should be removed after one
stable production release cycle on v3.
