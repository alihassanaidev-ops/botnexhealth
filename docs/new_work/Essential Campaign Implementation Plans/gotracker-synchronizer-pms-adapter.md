# GoTracker Synchronizer PMS Adapter

## Goal

Add GoTracker as a PMS integration path using the ScaleNexus GoTracker Synchronizer API, while keeping the existing universal PMS adapter contract intact.

## Scope

- Add `gotracker` as a supported `Institution.pms_type`.
- Store GoTracker configuration per location:
  - Synchronizer base URL.
  - Location-scoped product key.
- Implement a GoTracker PMS adapter for the shared PMS operations already used by setup/admin flows:
  - patients
  - providers
  - operatories
  - appointment types
  - available slots
  - appointment listing
  - appointment booking
  - appointment confirmation/cancellation
  - basic recall listing
- Expose GoTracker setup fields in the tenant/location admin UI.
- Add a location-scoped GoTracker webhook receiver:
  - verifies `X-ScaleNexus-Signature` using one global secret
  - stores durable raw/redacted webhook envelopes short-term
  - dedupes repeated deliveries
  - updates local patient/contact and appointment projections
  - triggers appointment workflows on new/rescheduled appointments
  - cancels pending workflow runs when an appointment is cancelled
- Auto-create GoTracker webhook subscriptions for configured locations:
  - worker task scans GoTracker locations with product keys
  - posts to `POST /api/webhooks/subscriptions`
  - creates one subscription per event using the documented `event_types` request field
  - uses the global signing secret and a location-scoped callback URL
  - tracks local subscription setup/health state

## Decisions

| Decision | Reason |
|----------|--------|
| Use the existing PMS adapter interface. | Keeps routes and setup pages provider-neutral instead of creating GoTracker-only code paths. |
| Store the product key per location. | GoTracker docs say the product key is issued per location. |
| Prefix external IDs with `gt-`. | Avoids collisions with NexHealth IDs while preserving source identity. |
| Do not implement `upsertContact` writes in this adapter. | GoTracker docs mark that endpoint as agent-auth, not product-key auth. |
| Use one global GoTracker webhook secret. | CTO decision on 2026-07-22; mirrors the current global NexHealth signing-secret model. |
| Use a location-scoped webhook URL. | Product keys are location-scoped, but webhook signatures are global; the callback path identifies the local location without trusting payload fields. |
| Let this app own subscription creation. | CTO decision on 2026-07-22: follow the NexHealth pattern and create subscriptions through the Synchronizer API. |

## Follow-Up

- Confirm real GoTracker webhook payload shapes in staging and adjust flexible field extraction if the Synchronizer emits different names.
- Confirm real PMS-agent-originated GoTracker event payloads in staging.
- Configure `GOTRACKER_WEBHOOK_CALLBACK_BASE_URL` and `GOTRACKER_WEBHOOK_SECRET` before running the worker in staging/prod.
