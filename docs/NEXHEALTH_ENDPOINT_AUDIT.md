# NexHealth Endpoint Audit (Current Backend Usage)

## Scope

This document inventories NexHealth endpoints currently used by this codebase and maps them to:

- Internal API routes (`/api/v1/nexhealth/*`)
- Adapter-driven routes (`/api/v1/pms/*`, `/tenant/setup/*`)

No code changes are included. This is documentation-only.

## Sources

- Backend route and adapter code in:
  - `src/app/api/routes/*.py`
  - `src/app/api/routes/universal/*.py`
  - `src/app/pms/nexhealth/adapter.py`
- Live NexHealth API reference:
  - [Onboardings](https://docs.nexhealth.com/v20240412/reference/onboardings)
  - [View institutions](https://docs.nexhealth.com/v20240412/reference/getinstitutions)
  - [View location appointment descriptors](https://docs.nexhealth.com/v20240412/reference/getlocationsidappointmentdescriptors)
  - [View available slots](https://docs.nexhealth.com/v20240412/reference/getavailableslots)
  - [Sync status](https://docs.nexhealth.com/v20240412/reference/getsyncstatus)
  - [View webhook endpoints](https://docs.nexhealth.com/v20240412/reference/getwebhookendpoints)
  - [Create webhook endpoint](https://docs.nexhealth.com/v20240412/reference/postwebhookendpoints)
  - [View webhook subscriptions](https://docs.nexhealth.com/v20240412/reference/getwebhookendpointsidwebhooksubscriptions)
  - [Create webhook subscription](https://docs.nexhealth.com/v20240412/reference/postwebhookendpointsidwebhooksubscriptions)
  - [Get started with scheduling](https://docs.nexhealth.com/v20240412/docs/book-an-appointment)

## Current NexHealth Endpoints in Use

### Practice Overview

| Internal API | Method | NexHealth path | Notes |
|---|---|---|---|
| `/api/v1/nexhealth/institutions` | GET | `/institutions` | Used directly and by adapter location listing. |
| `/api/v1/nexhealth/institutions/{institution_id}` | GET | `/institutions/{id}` | Institution detail lookup. |
| `/api/v1/nexhealth/locations` | GET | `/locations` | Supports subdomain and filters. |
| `/api/v1/nexhealth/locations/{location_id}` | GET | `/locations/{id}` | Single location details. |
| `/api/v1/nexhealth/locations/{location_id}/appointment_descriptors` | GET | `/locations/{id}/appointment_descriptors` | Used for descriptor mapping setup. |
| `/api/v1/nexhealth/providers` | GET | `/providers` | Supports includes for availability/type context. |
| `/api/v1/nexhealth/providers/{id}` | GET | `/providers/{id}` | Single provider detail. |
| `/api/v1/nexhealth/operatories` | GET | `/operatories` | Required for operatory-aware booking flows. |
| `/api/v1/nexhealth/operatories/{operatory_id}` | GET | `/operatories/{id}` | Single operatory detail. |
| `/api/v1/nexhealth/patients` | GET | `/patients` | Search by name/email/phone/DOB. |
| `/api/v1/nexhealth/patients/{id}` | GET | `/patients/{id}` | Patient detail. |
| `/api/v1/nexhealth/patients` | POST | `/patients` | Patient creation. |

### Scheduling

| Internal API | Method | NexHealth path | Notes |
|---|---|---|---|
| `/api/v1/nexhealth/appointment_types` | GET | `/appointment_types` | List types; descriptor includes used. |
| `/api/v1/nexhealth/appointment_types` | POST | `/appointment_types` | Create type. |
| `/api/v1/nexhealth/appointment_types/{id}` | GET | `/appointment_types/{id}` | Detail lookup. |
| `/api/v1/nexhealth/appointment_types/{id}` | PATCH | `/appointment_types/{id}` | Update type. |
| `/api/v1/nexhealth/appointment_types/{id}` | DELETE | `/appointment_types/{id}` | Delete type. |
| `/api/v1/nexhealth/appointment_types/{id}/appointment_descriptors` | GET | `/appointment_types/{id}/appointment_descriptors` | Descriptor linkage checks. |
| `/api/v1/nexhealth/appointments` | GET | `/appointments` | Appointment listing by date window. |
| `/api/v1/nexhealth/appointments` | POST | `/appointments` | Booking flow. |
| `/api/v1/nexhealth/appointments/{id}` | PATCH | `/appointments/{id}` | Cancel/update flow. |
| `/api/v1/nexhealth/appointment_slots` | GET | `/appointment_slots` | Slot search used in voice booking. |
| `/api/v1/nexhealth/availabilities` | GET | `/availabilities` | Availability retrieval. |
| `/api/v1/nexhealth/availabilities/{availability_id}` | GET | `/availabilities/{id}` | Availability detail. |

### Adapter-only NexHealth Writes (not exposed as direct `/api/v1/nexhealth/*` routes)

| Internal Surface | Method | NexHealth path | Notes |
|---|---|---|---|
| `/api/v1/pms/setup/availability` and `/tenant/setup/availabilities/{source_id}` | POST/PATCH | `/availabilities`, `/availabilities/{id}` | Used for availability linking/update workflows. |
| `/api/v1/pms/appointment-types` and `/tenant/setup/appointment-types` | POST | `/appointment_types` | Adapter-driven creation path. |
| `/tenant/setup/appointment-types/{source_id}` | DELETE | `/appointment_types/{id}` | Delete via adapter client passthrough. |

## Multi-Tenant Behavior in Current Code

- Tenant and location context are resolved first, then used to scope NexHealth calls via `subdomain` + `location_id`.
- Universal adapter routes (`/api/v1/pms/*`) and tenant setup routes (`/tenant/setup/*`) are tenant-aware wrappers around these same NexHealth paths.
- `pms_write_enabled` behavior is required by product SOP but is currently documented as a roadmap concern for strict enforcement and fallback messaging across all write paths.

## Important Live Reference Note

NexHealth reference currently has `GET /available_slots` documented as “View available slots” while this backend uses `GET /appointment_slots`. NexHealth guides still show `appointment_slots` usage in scheduling examples. Validate final canonical path during implementation hardening.

