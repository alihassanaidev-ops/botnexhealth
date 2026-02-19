# PMS Support Impact For NexHealth Endpoints

## Purpose

Summarize how endpoint support varies by PMS using the local JSON matrix in:

- `docs/Supported_API_Per_PMS_Nexhealth/*.json`

This is used to decide which SOP features can be universal vs must be capability-gated/fallback.

## Dataset

- PMS count: 17
- Source files: Athena, Cloud9, Curve, Denticon, Dentrix, Dentrix Ascend, Dentrix Enterprise, Dolphin, DrChrono, Eaglesoft, ModMed, NextGen Office, OpenDental, Orthotrac Local, Practiceworks, QDW - QSI Dental Web, eClinicalWorks.

## Core Endpoint Support (Used Today)

| Endpoint capability (JSON label) | Yes | Partial | No | Impact |
|---|---:|---:|---:|---|
| View Institutions | 17 | 0 | 0 | Safe universal. |
| View locations | 17 | 0 | 0 | Safe universal. |
| View patients / Create patient / View patient | 17 | 0 | 0 | Safe universal. |
| View Providers / View Provider | 17 | 0 | 0 | Safe universal. |
| View appointment types (+ CRUD) | 17 | 0 | 0 | Safe universal. |
| View appointments / Create appointment | 17 | 0 | 0 | Safe universal. |
| Edit Appointment | 11 | 6 | 0 | Reschedule/cancel reliability varies by PMS. |
| View appointment slots | 17 | 0 | 0 | Safe universal. |
| View availabilities / View availability / Edit availability / Delete availability | 17 | 0 | 0 | Safe universal. |
| View operatories / View operatory | 12 | 0 | 5 | Must support no-operatory fallback paths. |
| View location Appointment descriptors | 10 | 2 | 5 | Descriptor-linked flows need fallback. |
| View appointment type appointment descriptors | 6 | 6 | 5 | Descriptor mapping is highly PMS-dependent. |

## Expansion Endpoint Support (SOP D1-D2)

| Endpoint capability (JSON label) | Yes | Partial | No | Impact |
|---|---:|---:|---:|---|
| View sync statuses | 17 | 0 | 0 | Good fit for D1 operational checks. |
| View/Create/Edit/Delete webhook subscriptions | 17 | 0 | 0 | Good fit for event-driven architecture across PMS options. |
| View guarantor balances | 4 | 0 | 13 | D2 balance campaigns require strict clinic-level gating. |
| Create Payment | 4 | 0 | 13 | PMS write-back for Stripe is not universal. |
| View payment types / payment type | 4 | 0 | 13 | Payment UX enrichment is limited to a minority of PMSs. |

Supported for financial endpoints (`yes` group): Dentrix, Dentrix Enterprise, Eaglesoft, OpenDental.

## PMS Groups Requiring Explicit Fallbacks

### No operatories support (5 PMS)

- Athena
- ModMed
- NextGen Office
- QDW - QSI Dental Web
- eClinicalWorks

Fallback: keep booking/rescheduling logic operatory-optional and do not block appointment workflows.

### No appointment descriptor support (5 PMS, plus 6 partial for appointment-type descriptors)

- No support: Curve, Dolphin, DrChrono, Practiceworks, QDW - QSI Dental Web
- Partial support subset exists in additional PMSs

Fallback: appointment type creation/linking should degrade gracefully when descriptor APIs are unavailable or partial.

### No ledger/payment endpoint support (13 PMS)

- Athena, Cloud9, Curve, Denticon, Dentrix Ascend, Dolphin, DrChrono, ModMed, NextGen Office, Orthotrac Local, Practiceworks, QDW - QSI Dental Web, eClinicalWorks

Fallback: disable automated balance campaigns and payment write-back for unsupported clinics.

## Product/Architecture Implications

1. Keep `pms_write_enabled` enforced for all mutating actions.
2. Add capability flags per clinic/adapter and gate endpoint usage dynamically.
3. Treat descriptor and operatory dependency as optional, not required, in voice flows.
4. Restrict D2 balance automation to supported ledger PMSs unless alternate adapter support exists.
5. Log unsupported capabilities as expected outcomes (not errors) in audit and operational logs.

## Suggested Runtime Capability Model

Use adapter-reported booleans:

- `supports_operatories`
- `supports_location_descriptors`
- `supports_appointment_type_descriptors`
- `supports_edit_appointment`
- `supports_sync_status`
- `supports_webhook_subscriptions`
- `supports_ledger_balances`
- `supports_payment_writeback`

This should drive:

- Function-call routing in live AI interactions
- Dashboard feature visibility
- Campaign scheduler eligibility
- SOP-compliant fallback messaging (`needs_callback`, insurance fallback, read-only PMS behavior)

