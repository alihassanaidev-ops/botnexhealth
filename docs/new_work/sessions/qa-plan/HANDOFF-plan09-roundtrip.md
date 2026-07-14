# HANDOFF — Plan 09 final round-trip (resume point, 2026-07-15)

> **✅ EXECUTED / DONE (2026-07-15).** This handoff has been completed: the real NexHealth webhook round-trip
> passed live via a cloudflared tunnel (book → `appointment_insertion`, cancel → `appointment_updated`, both
> received/verified/projected end-to-end). **Plan 09 = 100%.** See `plan-09-staging-results.md` (Flow 5). The
> "~96%" and "last ~4%" figures below are pre-execution and kept for history only.

**Goal:** close the last ~4% of Plan 09 — prove a REAL NexHealth webhook round-trip (book an appointment →
NexHealth fires the webhook → our app receives + processes it). Everything else is done + verified.

## Where we are
- Plan 09 is **~96%**. Auth, backfill, subscription registration, inbound signature + parsing all **fixed +
  verified live** against the NexHealth sandbox. See `plan-09-staging-results.md` for full detail.
- **3 real bugs found + fixed this session** (committed by the user, or staged): appointments `start_date/end_date`
  → `start/end`; webhook registration reworked to `/webhook_endpoints` 2-step flow; inbound parser reworked to
  `event_name` + `{timestamp}.{base64(body)}` signature. All were v2.0-era-code vs current-v2 (v2.2.2) drift.
- The **only thing left** is the live round-trip. It was blocked because **ngrok can't hold a stable tunnel in
  this env** (CRL fetch "network is unreachable"). **Use `cloudflared` instead.**

## The tenant IS bookable (verified)
Sandbox `silora-demo-practice`, location `348511`:
- provider `485073654` (Brian Albert), appointment_type `1197701` (Checkup/Cleaning), operatory `268375`
- patient `495966225` (ABDULLAH KARIM, has email), and **open slots** e.g. `2026-07-23T08:00:00.000-07:00`.

## Exact steps to finish
1. Install + start cloudflared: `sudo pacman -S cloudflared` then `cloudflared tunnel --url http://localhost:8000`
   → note the public `https://<x>.trycloudflare.com` URL. (The API container is already up on :8000; if not, `make up-app`.)
2. Register a subscription pointing at `<cf-url>/api/v1/nexhealth/webhooks/appointments`:
   - `POST /webhook_endpoints {"target_url": ...}` → returns `{id, secret_key}` (JSON, Nex-Api-Version: v2).
   - per event: `POST /webhook_endpoints/{id}/webhook_subscriptions?subdomain=silora-demo-practice`
     `{"resource_type":"Appointment","event": "appointment_insertion" | "appointment_updated"}`.
3. (For full processing, optional) wire a local `InstitutionLocation` to `nexhealth_location_id=348511` /
   `nexhealth_subdomain=silora-demo-practice`, set `NEXHEALTH_WEBHOOK_SECRET=<secret_key>` in `.env`, restart the
   api container, and run a Celery worker. Without this the webhook is received but responds "unknown location"
   (still proves delivery + signature + parsing). With it, the projection upserts + enrollment enqueues.
4. Book: `POST /appointments?subdomain=silora-demo-practice&location_id=348511&notify_patient=false`
   body `{"appt":{"patient_id":495966225,"provider_id":485073654,"appointment_type_id":1197701,
   "operatory_id":268375,"start_time":"2026-07-23T08:00:00.000-07:00","location_id":348511}}`.
5. Verify: `docker logs botnexhealth-api --since 60s | grep -i webhook` (should show the received
   `appointment_insertion.complete`); check the `appointment_working_set` table for the upserted row.
6. **CLEANUP (important — sandbox writes):** cancel the appointment (`PATCH /appointments/{id}` `{"appt":{"cancelled":true}}`)
   and delete the endpoint (`DELETE /webhook_endpoints/{id}` → 204). Clear `NEXHEALTH_WEBHOOK_SECRET` if you set it.

## Key facts / standing rules
- **Sandbox creds are in `.env`** already: `NEXHEALTH_API_KEY` = `dXNlci0xMzA2LXNhbmRib3g.eo0TQORAig1lpVRvl75u2doxvTX1UKUO`
  (this is the API KEY, not a webhook secret — the CTO's value), `NEXHEALTH_SUBDOMAIN=silora-demo-practice`,
  `NEXHEALTH_LOCATION_ID=348511`. Base URL `https://nexhealth.info`, `Nex-Api-Version: v2`.
- Tests: `APP_ENV=local WEBAUTHN_RP_ID=localhost uv run pytest ...`; DB on host `:5433`; migrations
  `DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/nexhealth APP_ENV=local uv run alembic upgrade head`.
- **Alembic revision ids must be ≤ 32 chars.**
- **Do NOT commit — provide the commit message + description; the user commits.** For `git add`, docs under
  `docs/new_work/` need `git add -f` (local `.git/info/exclude` hides new docs). Code/tests are not excluded.
- **We are staying on NexHealth v2** (not migrating to v2.2/v3). The v2→v3 changes doc + three-way comparison
  live at `docs/nexhealth-v2-to-v3-changes.docx` and `docs/nexhealth-version-comparison.docx`.
- After the round-trip passes → Plan 09 = 100%; also close D-6 (recall projection decision) if data allows.

## To resume in a new session
`claude --resume` (this session, full history) OR a blank session: "read this HANDOFF + `plan-09-staging-results.md`
+ `CLAUDE.md`, then run the cloudflared round-trip."
