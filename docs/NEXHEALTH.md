# NexHealth Integration

Last reviewed: June 2026. Code lives in `src/app/nexhealth/` (transport: auth,
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
`GET /appointments` and `GET /appointment_slots`, 1000 req/min for
patient/appointment endpoints, 2000 req/min otherwise.

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

## Slot search and booking

Raw availability comes from `GET /appointment_slots` (response is nested per
location/provider; the adapter flattens it). We then filter locally
(`src/app/services/slot_filter.py`):

1. Buffer: drop slots starting before `now + provider.buffer_minutes`.
2. Operating hours + breaks: per-day windows configured on the location,
   evaluated in the clinic's timezone.
3. Same-day cutoff: if the provider has *no* appointments booked today and the
   current time is past their cutoff, hide all remaining same-day slots (the
   provider probably isn't coming in). The "any appointments today?" check
   queries NexHealth live and **fails safe**: on any error it assumes
   appointments exist and leaves slots visible.

Booking is `POST /appointments` (body wrapped under `"appt"`), cancel is
`PATCH /appointments/{id}` with `cancelled: true`. **Reschedule books the new
slot first, then cancels the old one** — if the new booking fails the patient
keeps their original appointment; if the cancel fails after a successful
booking we return success with a warning rather than unwinding the new booking
(`src/app/pms/nexhealth/adapter.py`).

There is no slot-level double-booking guard beyond what NexHealth/the PMS
enforces; two agents racing for the same slot resolve at NexHealth's side.

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
(windows minus existing appointments) and come from `GET /appointment_slots`.
Don't reach for `/availabilities` when you mean "what can the patient book".

**`/availabilities` returns empty for PMS-synced schedules.** For providers
whose schedule syncs from the PMS, the endpoint can return 200 with zero rows
even though the provider has working hours. The same windows *are* embedded in
`GET /providers?include[]=availabilities`, so `list_availabilities()` merges
both sources by ID (`adapter.py:580-614`). Without this, the setup UI shows
providers as having no hours.

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

**Inconsistent response nesting.** `GET /patients/{id}` may put the record
under `data.user`, `data.patient`, or directly in `data` — we try all three
(`adapter.py:211-215`). Patient phone/DOB may be top-level or under `bio`
(`mappers.py:89-90`). Appointment-type duration arrives as `minutes` or
`duration` (`mappers.py:139`). List endpoints nest under a plural key
(`data.patients`), writes must wrap the body under the singular resource name
(`{"appointment_type": {...}}`) or you get `Missing parameter` back.

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
bookable slots (`adapter.py:280-306`).

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
`test_nexhealth_adapter_appointments.py`, `test_slot_filter.py`, and
`tests/integration/test_slot_duration_edge_cases.py`.

## Stable vs. new API

We pin NexHealth's stable API via the Accept header
(`application/vnd.Nexhealth+json;version=2`, `src/app/config.py:69`).
NexHealth has a newer API generation (currently beta) that addresses two of
the pain points above directly: the misleading names are fixed (what the
stable API calls "availabilities" is exposed as working windows), and
working-window sync is configurable through the API itself instead of
requiring a NexHealth support request per practice.

Migrating is a planned future improvement, not active work — the stable API
is what production runs on. When the evaluation happens, the work is contained
to the adapter and mappers (`src/app/pms/nexhealth/`); the `PMSAdapter`
interface and everything above it shouldn't need to change. The caveats list
above doubles as the regression checklist for that migration: each quirk
should be re-tested against the new API, and several should simply disappear.
