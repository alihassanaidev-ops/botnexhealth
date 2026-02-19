# NexHealth Multi-Tenancy And Expansion Endpoints

## Purpose

Define NexHealth endpoints needed beyond the currently integrated set, based on:

- Product SOP requirements (D1-D2 focus)
- Live NexHealth API reference
- Current backend endpoint inventory

## Sources

- SOP text provided in-thread (ScaleNexusAI Development SOP v3.0, Feb 2026)
- Live NexHealth docs:
  - [Onboardings overview](https://docs.nexhealth.com/v20240412/reference/onboardings)
  - [Create onboarding](https://docs.nexhealth.com/v20240412/reference/postonboardings)
  - [View onboardings](https://docs.nexhealth.com/reference/getonboardings)
  - [Get started: create onboarding + credentials](https://docs.nexhealth.com/v20240412/docs/create-your-first-onboarding-api-credentials)
  - [Sync status](https://docs.nexhealth.com/v20240412/reference/getsyncstatus)
  - [View webhook endpoints](https://docs.nexhealth.com/v20240412/reference/getwebhookendpoints)
  - [Create webhook endpoint](https://docs.nexhealth.com/v20240412/reference/postwebhookendpoints)
  - [View webhook subscriptions](https://docs.nexhealth.com/v20240412/reference/getwebhookendpointsidwebhooksubscriptions)
  - [Create webhook subscription](https://docs.nexhealth.com/v20240412/reference/postwebhookendpointsidwebhooksubscriptions)
  - [Edit webhook subscription](https://docs.nexhealth.com/reference/patchwebhookendpointsidwebhooksubscriptionshashid)
  - [Delete webhook subscription](https://docs.nexhealth.com/reference/deletewebhookendpointsidwebhooksubscriptionshashid)
  - [Guarantor balances](https://docs.nexhealth.com/reference/guarantor-balances)
  - [Payment transactions guide](https://docs.nexhealth.com/docs/payments)
  - [Book an appointment guide](https://docs.nexhealth.com/v20240412/docs/book-an-appointment)

## Priority Endpoint Plan

### P0: D1 Multi-Tenant Onboarding/Operations

| Endpoint | Why it is needed | SOP mapping |
|---|---|---|
| `POST /onboardings` | Programmatic institution onboarding and credential lifecycle kickoff. | D1 onboarding UI + multi-tenant setup. |
| `GET /onboardings` | Admin visibility into onboarding status across clinics. | D1 onboarding operations and troubleshooting. |
| `GET /onboardings/{id}` | Poll/check single clinic onboarding progress. | D1 onboarding flow status checks. |
| `GET /sync_status` | Verify PMS sync health and integration readiness. | D1 reliability/operational checks. |
| `GET /webhook_endpoints` | Determine existing webhook infrastructure per tenant. | D1 post-call and appointment-event ingestion strategy. |
| `POST /webhook_endpoints` | Register backend receiver URLs per clinic/integration. | D1/D2 event-driven architecture. |
| `GET /webhook_endpoints/{id}/webhook_subscriptions` | Audit currently subscribed event types. | D1 operational readiness + D2 campaigns. |
| `POST /webhook_endpoints/{id}/webhook_subscriptions` | Subscribe to appointment/patient/sync events. | D1 call outcomes + D2 campaign triggers. |
| `PATCH /webhook_endpoints/{id}/webhook_subscriptions/{hashid}` | Change subscriptions without recreating endpoint. | D1-D2 lifecycle operations. |
| `DELETE /webhook_endpoints/{id}/webhook_subscriptions/{hashid}` | Safe unsubscribe for offboarding/reconfiguration. | D1 tenant deactivation/offboarding controls. |

### P0: D1 Insurance Verification

| Endpoint family | Why it is needed | SOP mapping |
|---|---|---|
| Insurance coverage retrieval endpoints (`insurance_coverages` family) | Baseline coverage read path for `check_insurance` feature. | D1 `check_insurance` function behavior. |
| Eligibility verification endpoint (if enabled for PMS/institution) | Real-time verified/unverified decision in-call. | D1 insurance verification requirement. |

Implementation note: public v20240412 reference clearly exposes insurance coverage resources, but eligibility availability is integration-dependent. Use capability detection and required fallback to clinic `accepted_insurance` config when eligibility is unsupported/unavailable.

### P1: D2 Financial/Balance Collection

| Endpoint | Why it is needed | SOP mapping |
|---|---|---|
| `GET /guarantor_balances` | Identify overdue balances for outreach campaigns. | D2 balance reminder campaigns. |
| `GET /guarantor_balances/{id}` | Drill-in for specific guarantor balance detail. | D2 payment context and UI drilldowns. |
| `POST /payment_transactions` | Write Stripe-collected payments back into PMS ledger. | D2 Stripe webhook -> PMS ledger sync. |
| Optional supporting finance reads (`charges`, `payments`, `payment_types`, `insurance_balances`) | Enhanced financial reconciliation and reporting where available. | D2 dashboard metrics and reconciliation hardening. |

## Capability Flags Required In App Layer

Add and enforce per-clinic feature flags at adapter capability level:

- `supports_onboarding_api`
- `supports_sync_status_api`
- `supports_webhook_subscriptions_api`
- `supports_insurance_eligibility`
- `supports_financial_ledger_reads`
- `supports_payment_transaction_writes`
- existing: `pms_write_enabled`

These flags should gate behavior, control fallbacks, and prevent failed live-call flows.

## Gap Summary Against Current Backend

Currently integrated in code:

- Core scheduling + patients + providers + institutions + availabilities.

Not yet integrated (priority from SOP):

- Onboarding lifecycle endpoints (`/onboardings*`)
- Sync status endpoint (`/sync_status`)
- Webhook endpoint/subscription lifecycle endpoints
- Financial ledger + payment transaction writeback endpoints
- Explicit eligibility capability negotiation/fallback orchestration

## Recommended Documentation/Implementation Sequence

1. Add onboarding + sync + webhook endpoint docs and request/response contracts first.
2. Add insurance verification behavior contract with strict fallback rules.
3. Add financial endpoints documentation with PMS-support gating rules.
4. Add failure-handling matrix (unsupported vs timeout vs auth failure) for each endpoint family.

