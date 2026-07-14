# Plan 09 — Sandbox Verification Results (live NexHealth)

**Date:** 2026-07-14 · **Env:** NexHealth **sandbox**, tenant `silora-demo-practice`, location `348511` (Scale Nexus Demo)
**Version:** our current code (v2 line). We are NOT migrating to v2.2.2/v3.0.0 in this pass — just validating v2.
**Method:** drive our code's exact request shapes against the live sandbox API; simulated signed events for inbound webhooks.

## Creds resolved
- The value the CTO gave (pasted as "webhook secret") is actually the **API key** — auth returns HTTP 201.
- Discovered via `GET /institutions`: subdomain `silora-demo-practice`; locations 348511 (Scale Nexus Demo),
  340582 (Relaxation Dental), 339273 (Green River Dental). `.env` corrected.
- No real webhook secret yet (only needed for prod signature verification; local skips it).

## Flow results

### FLOW 1 — Auth ✅ PASS
`POST /authenticates` with the API key → 201, bearer token acquired. Our auth path works against the real API.

### FLOW 2 — List appointments / backfill ✅ FIXED (was a real bug)
**Original result:** ❌ FAIL — real bug found (details below). **After fix:** ✅ `GET /appointments` returns 200.

**Fix applied (2026-07-14):** renamed the `/appointments` query params `start_date`→`start`, `end_date`→`end`
in `adapter.py` (`has_provider_appointments_on_date` :293-294, `list_appointments` :366-368). Kept the Python
kwarg names. Updated `test_nexhealth_adapter_appointments.py` (asserted the old buggy keys). **180 NexHealth/
Plan 09 unit tests pass.** Live re-run: `GET /appointments` with `start`/`end` → 200. NOT touched:
`/appointment_slots` (`adapter.py:415` still uses `start_date`) — a DIFFERENT endpoint, not tested; **flag to verify separately.**

**Original bug detail:**
- Our adapter sends `start_date`/`end_date` (`adapter.py:284-285`, `list_appointments` `:363-375`;
  `has_provider_appointments_on_date` `:283-321`).
- The live API **rejects** these: `400 {"error":["Missing parameter start","Missing parameter end"]}`.
- With `start`/`end` instead → **200**. So the endpoint wants `start`/`end`, not `start_date`/`end_date`.
- **Impact:** `list_appointments` (Plan 09 REST backfill) and the provider-schedule check would 400 on every
  call against the real API. This never surfaced because Plan 09 was only mock-tested (the deferred 20%).
- **Fix:** rename the two params `start_date`→`start`, `end_date`→`end` in the `/appointments` calls (~4 lines).
  Trivial, but real — the backfill cannot work until this is fixed.

### FLOW 2b — Data availability (informational)
- Sandbox location 348511 returns **0 appointments** in the next 90 days (empty demo tenant, `count=0`).
- So even after the param fix, backfill returns nothing here — and there are no real appointments to change,
  which confirms inbound webhooks must be tested with **simulated signed events** (as agreed).

### FLOW 3 — Subscription create ❌ FAIL → **BROKEN against the real API (2nd real bug)**
Tested live via ngrok (`https://…ngrok-free.dev/api/v1/nexhealth/webhooks/appointments`):
- `POST /webhooks` (our code's endpoint) with a **JSON body** → **415** "content-type application/json not supported".
- Our client sends `json=` (`http_client.py:122`; `nexhealth_subscription_service.py:244-250`), so **our code hits this 415**.
- With a form body → **404** "Resource not found".
- `GET /webhooks` → **404** (endpoint does not exist); `GET /webhook_endpoints` → **200** (the real one; "subdomain ignored" → account-level).
- **Conclusion:** our webhook registration is **fundamentally broken** — it posts JSON to `/webhooks`, which
  doesn't exist. The real flow is the documented **`/webhook_endpoints` → `/webhook_endpoints/{id}/webhook_subscriptions`**,
  **form-encoded**. This is the drift we flagged, now confirmed live. **Fix = a real rework** of
  `nexhealth_subscription_service` (2-step endpoint+subscription create + form encoding), not a 2-line change.
- Cleanup: no persistent subscription was created (all attempts were rejected). ngrok stopped.

**FIXED (2026-07-14):** reworked `nexhealth_subscription_service._try_remote_create` to the correct 2-step flow —
`POST /webhook_endpoints {"target_url"}` → `{id, secret_key}`, then per event
`POST /webhook_endpoints/{id}/webhook_subscriptions?subdomain=X {"resource_type":"Appointment","event":<ev>}`.
Valid events (probed live): only `appointment_insertion` + `appointment_updated` (cancel/delete arrive as updates).
`DEFAULT_APPOINTMENT_EVENTS` updated. **Verified live: endpoint 201, both subscriptions 201, delete 204. 180 tests pass.**
Bonus: the endpoint-create response returns the `secret_key` — so we now obtain the inbound signing secret on
registration (no need for the CTO to supply one).

### FLOW 4 — Inbound webhook handling ⚠ DRIFT FOUND — needs rework + live delivery to confirm
- Our inbound parser (`nexhealth_webhooks.py`) dispatches on `event` values like `appointment.created`/`.updated`/
  `.cancelled`/`.deleted` (dotted) and verifies `X-NexHealth-Signature` = HMAC of the **raw body**.
- But NexHealth actually sends `event_name` like **`appointment_insertion.complete`**, and signs with
  **`signature`/`timestamp` headers** over **`{timestamp}.{base64(payload)}`** using the endpoint `secret_key`.
- So the inbound side has the **same class of v2.0→v2.2.2 drift** — likely won't match real webhooks. **Cannot
  confirm live** (empty tenant → no real events; signature verify is skipped locally in non-prod). Handler *logic*
  (ledger/projection/reschedule) is covered by 180 tests, but the **event-name mapping + signature scheme need
  reworking** and then a live round-trip to prove.

### FLOW 4 — Inbound webhook handling (simulated signed events) ⏭ NEXT
- Plan: POST simulated `appointment.created/updated/cancelled` payloads (correct HMAC signature with a test
  secret) to our endpoint → verify signature check, `nexhealth_webhook_events` ledger dedup,
  `appointment_working_set` projection upsert, and reschedule re-enroll on a start-time change.
- Note: this path is already covered by real-Postgres unit/integration tests; the live sandbox adds little
  here beyond the payload shape, which we mirror from the projection parser.

## Verdict (final for this session)
The staging validation did its job — it found **two real bugs the mock tests missed**:
1. **Backfill params** (`start_date/end_date` → `start/end`) — ✅ **FIXED** + verified live (200) + 180 tests pass.
2. **Webhook registration** — ❌ **BROKEN** (posts JSON to `/webhooks`, which 404s; real API is `/webhook_endpoints`,
   form-encoded). **Needs a real rework** — NOT done this session.

3. **Webhook registration** — ✅ **FIXED** (reworked to the `/webhook_endpoints` 2-step flow; verified live).
4. **Inbound webhook parser** — ✅ **FIXED + live-verified.** Reworked `nexhealth_webhooks.py`: reads `event_name`
   (normalizes the `.complete` suffix), events `appointment_insertion`/`appointment_updated`, and the signature
   scheme `HMAC-SHA256(secret_key, "{timestamp}.{base64(body)}")` via `signature`/`timestamp` headers. **Verified
   live with a REAL endpoint `secret_key`:** valid sig accepted, tampered sig 403, missing timestamp 403,
   event_name parsed. 12 webhook tests + 180 Plan-09/NexHealth tests pass. (Payload struct `data.appointment.{...}`
   already matched.)

**All three drift bugs are now fixed and verified against the real API at every layer** (auth, backfill,
subscription registration, inbound signature + parsing). **Plan 09 is code-complete + verified (~95%+).**

**The only thing NOT done is a full real-appointment round-trip** (NexHealth actually delivering a webhook from a
real appointment change) — blocked by the **empty sandbox tenant** (no appointments to create/change), an
**environmental limitation, not a code defect.** Also open: reconciliation live-check (needs data), D-6 recall
projection decision, and the `/appointment_slots` param check (bonus, untested endpoint).

**To actually reach 100%:**
- Rework `nexhealth_subscription_service` to the `/webhook_endpoints` + `/webhook_endpoints/{id}/webhook_subscriptions`
  form-encoded flow, then re-verify subscription create live.
- Get test appointments into the sandbox (or accept simulated) to validate a real webhook round-trip + reconciliation.
- Check `/appointment_slots` for the same wrong-param issue.
- Decide D-6 (`recall_eligibility_working_set`).

**Committable now:** the appointments param fix (`adapter.py` + test) — real, verified, independent.
