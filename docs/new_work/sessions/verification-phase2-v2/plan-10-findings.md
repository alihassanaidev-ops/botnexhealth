# Plan 10 — Per-Tenant Messaging Provisioning — Verification Findings

**Audited:** 2026-07-03 against branch `ali/phase-2`.
**Plan:** `docs/new_work/Implementation Plans/10-per-tenant-messaging-provisioning.md`
**Session:** `docs/new_work/sessions/outbound-10-provisioning/`

## Summary

The implementation is a **deliberate MVP subset** of a very large plan. The session
docs (`task_plan.md`, `progress.md`) are honest about this: they redefine the goal to
"store and use per-institution Twilio creds + email from-address, with platform fallback,
Retell unchanged." Everything the *session* claims is real and verified. But measured
against the *plan* (7 tables, 6 services, A2P/10DLC, readiness model, domain provisioning,
vendor provisioning automation, webhook per-sub-account validation), roughly ~15-20% of the
plan's surface is delivered.

## What the plan called for (scope)

Data model (7 tables): `twilio_tenant_accounts`, `twilio_sender_numbers`,
`a2p_registration_records`, `email_sending_profiles`, `retell_tenant_voice_profiles`,
`messaging_readiness_checks`.
Services (6): `MessagingProvisioningService`, `TenantTwilioCredentialResolver`,
`TwilioProvisioningClient`, `EmailDomainProvisioningService`, `RetellProvisioningTracker`.
Plus: webhook signature validation by sub-account token, status sync job, readiness gating
of campaign publish, migrate `twilio_from_number` into `twilio_sender_numbers`, feature flags,
Secrets Manager references.

## What was actually built (evidence)

### Credential storage — REAL encryption confirmed
- `src/app/models/institution.py:224-229` — 4 new columns: `twilio_account_sid_encrypted`,
  `twilio_auth_token_encrypted` (Text), `email_from_address` (String 320), `email_from_name`
  (String 255).
- `src/app/models/institution.py:267-281` — encrypted property pairs `twilio_account_sid` and
  `twilio_auth_token` calling `encrypt_value`/`decrypt_value`.
- Encryption is genuine **AES-256-GCM**: `institution.py:86-107` (`encrypt_value`) uses
  `AESGCM` with a random 96-bit IV, key from `_get_encryption_key()` (`institution.py:55-75`).
  This is the same proven pattern as `nexhealth_api_key_encrypted`. **Not plaintext, not a stub.**
- NOTE: `email_from_address` / `email_from_name` are stored **plaintext** (they are not secrets
  — an address and display name — so this is acceptable).

### Migration
- `alembic/versions/20260703_institution_provisioning.py` — revision `20260703_provisioning`,
  down_revision `20260703_consent_channel`. Adds the 4 columns nullable, clean downgrade.

### SMS send path — per-institution creds with platform fallback
- `src/app/services/sms_service.py:44-64` — `_get_twilio_client(account_sid, auth_token)`:
  `sid = account_sid or settings.twillio_sid`, `token = auth_token or settings.twillio_api_secret`,
  raises `RuntimeError` if neither. Platform fallback per decision D2.
- `sms_service.py:203-206` — `send_sms` loads the `Institution` and passes
  `institution.twilio_account_sid` / `.twilio_auth_token` into `_get_twilio_client`.
  (Institution is already loaded for retention profile at `sms_service.py:120-127`.)

### Outbound email — per-institution from-address with platform fallback
- `src/app/services/automation/email_node_executor.py:68-73` —
  `from_address = (institution.email_from_address if institution else None) or settings.resend_from_email`,
  `from_name = institution.email_from_name`. API key remains platform-level
  (`settings.resend_api_key`) per decision D3 (per-institution API key deferred).

### Admin provisioning API (SUPER_ADMIN)
- `src/app/api/routes/admin_institutions.py:1564-1651`:
  - `ProvisioningStatusResponse` (1564), `ProvisioningUpdateRequest` (1571).
  - `_mask_sid` (1578) — masks to `AC12****cdef`.
  - `GET /{slug}/provisioning` (1585) — never returns auth token, only masked SID + email fields.
  - `PATCH /{slug}/provisioning` (1606) — sets creds via encrypted setters.
  - `DELETE /{slug}/provisioning/twilio` (1638) — clears Twilio creds.
  - All gated SUPER_ADMIN (`get_current_admin`) per decision D5.

### Tests
- `tests/unit/test_institution_provisioning.py` — 9 tests:
  - encrypt/decrypt round-trips for SID + token (lines 21-38), None handling (41-44).
  - `_mask_sid` normal/none/short (50-59).
  - SmsService uses institution creds when set (77-92), falls back to platform (95-113),
    raises when neither set (116-127).
- Tests are **unit-level with mocked Twilio Client and mocked settings** — they verify
  credential *selection* logic, not an end-to-end send. Session reports 1165/1165 unit tests
  passing (not re-run in this audit).

## Missing vs plan

| Plan deliverable | Status |
|---|---|
| `twilio_tenant_accounts` table | MISSING — replaced by 2 columns on `institutions` (no location scope, no status/region/parent-SID) |
| `twilio_sender_numbers` table | MISSING — still uses `InstitutionLocation.twilio_from_number` |
| `a2p_registration_records` (A2P 10DLC / toll-free) | MISSING entirely — no registration model, no status tracking |
| `email_sending_profiles` (SPF/DKIM/DMARC, warm-up) | MISSING entirely — only a from-address string |
| `retell_tenant_voice_profiles` | MISSING (deferred by design, decision D4 — single enterprise Retell account) |
| `messaging_readiness_checks` (readiness model/state) | MISSING entirely — no readiness computation anywhere |
| `MessagingProvisioningService` | MISSING |
| `TenantTwilioCredentialResolver` | MISSING as a service — resolution is inline in `sms_service.send_sms` |
| `TwilioProvisioningClient` (sub-account/number/brand/campaign APIs) | MISSING — creds are entered manually via admin API; no vendor provisioning automation |
| `EmailDomainProvisioningService` | MISSING |
| `RetellProvisioningTracker` | MISSING |
| Webhook validation by sub-account token | MISSING — see Bugs |
| Status sync job | MISSING |
| Readiness gating of campaign publish | MISSING here (may be partially in Plan 12 outbound-halt routes) |
| Migrate `twilio_from_number` → `twilio_sender_numbers` | N/A (table not built) |
| Feature flags per channel | MISSING |
| Secrets Manager references (plan preferred for prod) | MISSING — encrypted DB column only |

## Bugs / implementation gaps

1. **Twilio webhook signature validation does NOT use sub-account tokens** — latent bug.
   `src/app/api/routes/twilio_webhooks.py:168-180` (`_verified_form`) validates with
   `RequestValidator(settings.twillio_api_secret)` — the **platform** token only. The plan
   explicitly required (step 4) resolving the destination number → location/sub-account and
   validating with that sub-account's auth token. If any institution actually activates a
   sub-account, inbound-SMS and status-callback webhooks signed by that sub-account will
   **fail signature validation**. Today this is dormant because no real sub-accounts are
   provisioned, but it is a correctness gap the moment the feature is used.

2. **No validation that the sender number belongs to the resolved sub-account.** SMS creds
   come from `Institution` (`sms_service.py:203-206`) while `from_number` comes from
   `InstitutionLocation.twilio_from_number` (`sms_service.py:99`). Nothing guarantees the
   number is owned by the sub-account SID. A misconfiguration (sub-account creds + a
   platform-owned number, or vice versa) would fail at Twilio with an opaque error rather
   than a clear readiness/config check. This is exactly the "credential resolver mistakes"
   risk the plan flagged.

3. **No readiness model means the plan's core architecture decision is unmet** — "Provisioning
   state is first-class data, not only environment variables. Campaign activation depends on
   it." There is no `messaging_readiness_checks` and no readiness computation, so campaign
   activation cannot depend on provisioning readiness as designed.

## Architectural concerns

- **Institution-level Twilio creds, location-level numbers.** Creds live on `Institution`; the
  plan wanted an optional `location_id` on `twilio_tenant_accounts`. Multi-location institutions
  therefore share one sub-account credential set — acceptable for MVP but diverges from the
  plan's per-location isolation goal (edge case: "Multi-location institution wants ... distinct
  SMS numbers").
- **Manual credential entry, no vendor provisioning.** The whole "provisioning" automation
  (sub-account creation, number purchase, A2P/10DLC brand/campaign registration, domain
  verification) is absent. What exists is *credential storage + routing*, not *provisioning*.
  This is a reasonable phase-1 scope but the plan's title ("provisioning") oversells what shipped.
- **No Secrets Manager path.** Plan preferred Secrets Manager ARNs for prod rotation; only the
  encrypted DB column exists.

## Technical debt

- The `twillio_*` typo (settings `twillio_sid`, `twillio_api_secret`) persists and the plan warned
  against spreading it. New domain code (`twilio_account_sid`, etc.) correctly uses the right
  spelling, so the typo is contained to legacy settings — good.
- Credential resolution is inline in `SmsService` and duplicated conceptually in
  `email_node_executor`. When a real resolver is needed (webhook validation, voice/SIP), this
  logic will need extraction into the planned `TenantTwilioCredentialResolver`.

## Code quality observations

- Encryption reuse is clean and correct (AES-256-GCM, proven pattern).
- Admin API is properly SUPER_ADMIN-gated, masks SIDs, never returns the auth token — good
  secret-hygiene.
- Fallback semantics (empty field → platform creds) are explicit and tested.
- Naming avoids the legacy typo. Migration is reversible.

## Tests verdict

Exist and are focused: 9 unit tests covering encryption round-trips, SID masking, and
credential *selection* (institution vs platform vs none). They do **not** cover: webhook
per-sub-account validation (feature absent), end-to-end SMS/email send with tenant creds,
number/sub-account ownership consistency, or readiness (features absent). No integration or
RLS tests for provisioning (plan called for RLS + integration tests). Session claims full unit
suite green.

## Scope alignment verdict

**Partial by design.** The session honestly rescoped Plan 10 to a credential-storage-and-routing
MVP and delivered that slice cleanly and securely (real AES-256-GCM encryption, platform
fallback, SUPER_ADMIN admin API, per-institution SMS + email routing, tests). But the plan's
defining deliverables — the 6 provisioning tables, the 6 services, A2P/10DLC registration,
email domain (SPF/DKIM/DMARC + warm-up), the readiness model, vendor provisioning automation,
and per-sub-account webhook validation — are **not** implemented. The most important latent
defect is the webhook signature gap (#1), which will break the moment a real sub-account is used.
Estimate ~20% of plan scope delivered; ~90% of the *rescoped session goal* delivered.
