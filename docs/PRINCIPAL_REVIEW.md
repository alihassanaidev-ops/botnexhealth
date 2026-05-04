# Principal-Engineer Code Review — NexHealth Voice Agent Backend

**Date:** 2026-05-04
**Branch:** `feature/supabase-to-aws-migration`
**Compliance scope (assumed):** HIPAA + PIPEDA + provincial (PHIPA / PIPA / Law 25 / etc.) — `ca-central-1` deploy + `Jurisdiction` enum is CA-only. Apply provincial residency rules.

The codebase is more mature than typical for a pre-deployment startup: AES-256-GCM PHI encryption, append-only audit triggers, refresh-token replay detection, idempotency for both webhooks and mid-call function invocations, an explicit identity-gate before PHI reveal, scrubbed-only Retell ingest, signed Twilio webhooks. Strong bones. The findings below are the gaps that matter before you take real PHI.

---

## BLOCKERS — fix before any institution onboards

### B1. Cross-tenant PHI leak via global NexHealth fallback
**Where:** `src/app/pms/factory.py:28`, `src/app/pms/nexhealth/adapter.py:74`
**Issue:** `institution.nexhealth_api_key or global_settings.nexhealth_api_key`. A newly onboarded institution without its own NexHealth credentials silently uses the **platform-level** NexHealth API key. Its Retell agent will then `lookup_patient`, `book_appointment`, and `create_patient` against the wrong tenant's PMS. This is a HIPAA breach‑notification‑class bug.
**Fix:** Fail closed in the factory:
```python
if not institution.nexhealth_api_key:
    raise ValueError(f"Institution {institution.slug} has no NexHealth credentials")
```
Allow the global fallback only when `settings.app_env in {"local", "dev", "test"}`. Add a smoke test that a freshly created institution returns 4xx on any Retell function call until creds are set.

### B2. `ENCRYPTION_KEY` not enforced as distinct from `JWT_SECRET`
**Where:** `src/app/security.py:22`, `src/app/models/institution.py:60`, `.env.example` (missing entry)
**Issue:** `_get_encryption_key` raises if `ENCRYPTION_KEY` is unset, but `derive_secret_key` (used by phone hash, refresh-token lookup hash, retell log hash) falls through to `jwt_secret` when `encryption_key` is missing. There is no `.env.example` line for `ENCRYPTION_KEY`, so an operator wiring up production may forget it. Mixing key purposes makes JWT rotation an *unintentional PHI rotation event* — phone-hash lookups will silently break.
**Fix:** (1) Add `ENCRYPTION_KEY=` to `.env.example` with a generation command in a comment. (2) In `Settings.load_secrets_from_files`, raise if `is_production and not encryption_key`. (3) Document a key-rotation runbook (no DB has data yet — easiest moment in product life to define one). (4) Either separate the keyed-hash key from the JWT secret, or document explicitly that JWT rotation requires a phone-hash backfill.

---

## HIGH — fix before going live

### H1. Login email-enumeration timing oracle
**Where:** `src/app/api/routes/auth.py:285-296`
**Issue:** Non-existent users return 401 immediately; existing users incur a ~200 ms Argon2id verify. Attackers can enumerate accounts by response timing. Pair with the per-IP rate limit you already have, but the oracle still leaks at 10/minute.
**Fix:** On the `if not user:` branch, run a dummy verify against a constant Argon2 hash before raising:
```python
PasswordService.verify_password(data.password, _DUMMY_HASH)
```

### H2. INSTITUTION_ADMIN routes silently pick "first location" when `location_id` is omitted
**Where:** `src/app/pms/factory.py:99-120`
**Issue:** When an institution admin calls e.g. `POST /api/v1/patients` without `location_id`, the dependency picks the oldest active location and creates an adapter scoped to that location's NexHealth subdomain. Mutations land in the wrong subdomain; reads return wrong-location data. The admin has no signal this happened.
**Fix:** For institution-scoped routes that mutate PMS data (create/book/reschedule/cancel), require `location_id` in the request when the caller is `INSTITUTION_ADMIN`. Reject 400 if absent.

### H3. Middleware ordering — DB lookup runs before rate limiting
**Where:** `src/app/main.py:114-131`. Starlette runs middleware in **reverse** registration order; the current chain executes `RequestID → Institution → SlowAPI → SecurityHeaders → CORS`. So unauthenticated requests with arbitrary `X-Institution-Slug` headers issue a DB query (`get_by_slug`) **before** the rate limiter sees them. A trivial DoS amplification.
**Fix:** Either skip `InstitutionMiddleware` until after rate limit, or register order so that SlowAPI runs first. Easiest: register `SlowAPIMiddleware` last (so it runs first). Add an explicit comment about Starlette's reverse-order semantics — easy footgun.

### H4. Soft-deleted user email collision
**Where:** `src/app/models/user.py:62` (`unique=True`); `src/app/api/routes/auth.py:279`, `:420`, `:471`, plus admin/portal lookups in `routes/admin_institutions.py` and `routes/institution_portal.py`
**Issue:** `email` is unique, soft-delete uses `deleted_at`. Re-onboarding a previously-soft-deleted user fails with an integrity error. Worse, several call sites read users by email **without** filtering `deleted_at IS NULL` — login does, but `bootstrap_database`, `create_super_admin`, `invite_super_admin`, and several admin invite/lookup paths don't.
**Fix:** Add a partial unique index `WHERE deleted_at IS NULL` and normalize *all* email lookups through a helper that excludes soft-deleted rows. Migration:
```sql
DROP INDEX users_email_key;
CREATE UNIQUE INDEX users_email_active_unique ON users (email) WHERE deleted_at IS NULL;
```

### H5. RLS migration was abandoned without a stated alternative
**Where:** working tree shows `D alembic/versions/20260217_0002_enable_rls_tenant_isolation.py`. It never reached `HEAD`. Tenant isolation rests entirely on application-level `WHERE institution_id = …` filters.
**Issue:** A single missing `where(... institution_id == ...)` in any PHI table query (calls/contacts/sms_history_logs/audit_logs) is a tenant-leak. Defense-in-depth at the DB layer is the standard mitigation.
**Fix:** Either restore the RLS migration with a `SET LOCAL app.institution_id = ...` set inside `get_db_session_dep`, or write down the conscious decision *not* to use RLS plus the compensating controls (e.g., a CI test that grep-checks every PHI-table query has an `institution_id` predicate). Don't leave it ambiguous in the alembic graph.

### H6. `InstitutionService.get_location_by_slug` not tenant-scoped
**Where:** `src/app/services/institution_service.py:151-156`
**Issue:** Returns any location by slug across the whole DB. The middleware at `middleware/institution.py:82` checks `location.institution_id == institution.id` *after* the lookup — fine for that path, but the next caller of this method who skips the post-check has a cross-tenant probe primitive.
**Fix:** Make the function require `institution_id`: `get_location_by_slug(slug, institution_id)`. Update callers.

### H7. Account unlock is gated on SUPER_ADMIN; institution admins cannot help their own staff
**Where:** `src/app/api/routes/auth.py:725-731` (`get_current_admin` is aliased to SUPER_ADMIN at `deps.py:79`)
**Issue:** When a clinic staff member trips the lockout (5 failed attempts, 30-min lockout per `config.py:110-111`), the institution admin can't unlock them — only the platform super-admin can. That's a paging burden every weekend morning.
**Fix:** Add a tenant-scoped unlock route that an `INSTITUTION_ADMIN` can hit for `STAFF`/`LOCATION_ADMIN` users in their own institution. Audit-log it as `ACCOUNT_UNLOCK` with a clear actor.

### H8. Audit log `actor` field semantics are inconsistent
**Where:** `src/app/api/routes/calls.py:425`, `:484`, etc; vs. `src/app/api/routes/auth.py:286`, `:370`
**Issue:** Some call sites pass `actor=current_user.id` (a UUID), others pass `actor=AuditActor.API_CLIENT` (an enum), others pass `actor=user.role` (a role string). The field is meant to be one of the four `AuditActor` values; user identity belongs in `user_id`. As-is, audit reports filtering by actor are unreliable.
**Fix:** Treat `actor` as enum-only. Convert all call sites: `actor=AuditActor.ADMIN, user_id=str(current_user.id)`. Add a model-level CHECK constraint `actor IN ('RETELL_AGENT','ADMIN','SYSTEM','API_CLIENT')`.

---

## MEDIUM — fix soon, blocking quality bar

### M1. `.env.example` is dramatically incomplete
**Where:** `.env.example`
**Issue:** Missing: `ENCRYPTION_KEY`, `DATABASE_URL` (or the discrete DB_* set), `REDIS_URL`/`CELERY_BROKER_URL`, `COOKIE_SECURE`, `COOKIE_SAMESITE`, `CORS_ALLOWED_ORIGINS`, `TRUSTED_PROXY_CIDRS`, `AWS_S3_BUCKET_NAME`, `AWS_REGION`, `TWILLIO_SID`, `TWILLIO_API_SECRET`, `TWILIO_SMS_STATUS_CALLBACK_URL`, `RESEND_*`, `AUTH_FRONTEND_BASE_URL`. Production mistakes here are silent.
**Fix:** Rewrite `.env.example` to enumerate every key with comments and (where relevant) generation commands.

### M2. `derive_secret_key` mixes PHI and non-PHI key purposes
**Where:** `src/app/security.py:22`
**Issue:** Falls through to `jwt_secret` when `encryption_key` isn't set. PHI itself is protected by `institution.py:62` which raises, but `keyed_hash` callers (phone hash, retell log hash, idempotency arg hash, dead-letter hash, refresh-token hash) silently use the JWT secret. JWT rotation will then invalidate every phone-hash lookup — contacts created before rotation become unfindable by phone.
**Fix:** Raise in `derive_secret_key` if `encryption_key` is unset, or take an explicit `keyed_hash_secret` setting. Document key rotation procedure: rotating JWT must NOT touch `encryption_key` or hash-key material.

### M3. Audit log retention is undefined
**Where:** No code; HIPAA §164.316(b)(2) requires 6-year retention; provincial laws vary (Quebec: 5 years post-record-end).
**Issue:** No archival path, no retention DB job, no documented lifecycle. The DB will grow unbounded, and at scale you'll be tempted to truncate.
**Fix:** Define and document a retention policy. Options: nightly export to S3 Glacier with object-lock (compliance mode) + a soft 6-year DB retention. Track the runbook in `docs/`.

### M4. Twilio env vars are misspelled `TWILLIO_*`
**Where:** `src/app/config.py:96-97`
**Issue:** Real-world operators copy from Twilio docs (`TWILIO_*`). The misspelling is a foot-gun every onboarding will hit.
**Fix:** Rename to `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`. Keep one release of backward-compat reading by aliasing. Then drop the alias.

### M5. Call list tag filter is a substring ILIKE on a CSV column
**Where:** `src/app/api/routes/calls.py:357-363`
**Issue:** `Call.call_tags.ilike(f"%{tag}%")` won't use any index, and a tag named `complaint` matches any call_tag containing the substring. With ~100k calls this gets slow; with a tag named `complaint_resolved` (future) the query returns false positives.
**Fix:** Normalize `call_tags` to a `text[]` column or junction table; use `ANY(call_tags) = :tag`.

### M6. Phone search is silently disabled
**Where:** `src/app/api/routes/calls.py:386-393` (comment says "phone search would require decrypting all contacts")
**Issue:** Currently only name search is wired. Clinic staff will type phone numbers; behavior degrades to "no result".
**Fix:** Use the existing `phone_hash` column — call `hash_phone(search)` on the input and search by `Contact.phone_hash`. Document the partial-string limitation in the API or normalize input first.

### M7. Audit decorator's `_resolve_institution_id` reads a ContextVar that may already be reset
**Where:** `src/app/services/audit_decorator.py:201-209` + `src/app/retell/functions.py:191`
**Issue:** The wrapper runs `await func(...)` then resolves institution from the ContextVar. But the function runs to completion — if it raised before stashing institution onto the ContextVar (`update_call_context`), the audit row will be unscoped. Acceptable for Retell paths in practice (the resolve happens early), but worth a defensive fallback to `agent_id`-based lookup in the decorator.
**Fix:** When `institution_id` is unresolved on a Retell-actor audit row, look up `agent_id` from the call context as a last resort. Or include `agent_id_hash` in metadata so unscoped rows are still traceable.

### M8. Login allowed when `is_active=False` continues to increment `failed_login_attempts`
**Where:** `src/app/api/routes/auth.py:319`
**Issue:** Inactive accounts get their failed_login_attempts incremented and can be locked. Combined with H1, an attacker can detect inactive accounts (which still get the password-verify path) and DoS them into hard lockout. Minor on its own; combines with the timing oracle.
**Fix:** Don't increment failed counters for `is_active=False` (they can't log in anyway).

### M9. SSE ticket has no per-user/IP binding
**Where:** `src/app/services/event_bus.py:180-186`
**Issue:** A 32-byte ticket is single-use with 30s TTL — generally fine, but the redeemed ticket isn't bound to the requesting client (no IP/UA check). If a ticket leaks via referrer or browser extension, anyone can subscribe to that institution's events for 30s.
**Fix:** Bind ticket to user_id/institution_id (already there); also store the issuing IP and verify on redeem. Or move SSE auth to short-lived cookies.

### M10. `derive_secret_key` derivation cost is per-call
**Where:** `src/app/security.py:26-32`
**Issue:** HKDF-SHA256 is cheap, but the call site `keyed_hash` calls it on every hash. For caller-ID lookups (phone hash) this is one HKDF + one HMAC per write — acceptable. For audit decorator's `request_id` the cost is negligible. Just verify under load.
**Fix:** None now — note as a perf review item if Retell function call latency tightens.

---

## LOW — code smell / noise

### L1. `get_current_admin` aliases SUPER_ADMIN with a confusing name
**Where:** `src/app/api/deps.py:79-90`
**Issue:** Name implies "any admin"; behavior is "platform super-admin." Future engineer reads the dependency and assumes wrong privilege level.
**Fix:** Rename to `get_current_super_admin` everywhere; delete the alias. Or delete the alias and update callers — there are ~30, all in admin routes.

### L2. Retell signature verification accepts 3 body forms
**Where:** `src/app/retell/security.py:69-91`
**Issue:** Tries raw, stripped, and canonicalized. Stripped is a tolerance for trailing whitespace; canonical re-serializes the JSON. Both are belt-and-suspenders rather than a vuln, but expanding the accepted-signature surface should be intentional.
**Fix:** Document why all three exist (which proxy or environment caused the need). If "stripped" was added speculatively, drop it.

### L3. Egg-info has stale Supabase residue
**Where:** `src/nexhealth_voice_agent_backend.egg-info/{requires.txt,SOURCES.txt}`
**Issue:** `pip install -e .` will resurface `supabase>=2.0.0` if the wheel is rebuilt from this egg-info.
**Fix:** Regenerate via `pip install -e . --force-reinstall` after a clean `rm -rf src/*.egg-info`.

### L4. `RetellWebhookEvent` name collision (Pydantic vs SQLAlchemy)
**Where:** `src/app/retell/webhooks.py:72-75` vs. `src/app/models/retell_webhook_event.py`
**Issue:** Two classes named `RetellWebhookEvent`; the function imports the SQLAlchemy one inline (`webhooks.py:90`) to avoid the collision. Works, but the local import at function-call hides the model dependency from the import graph.
**Fix:** Rename the Pydantic model to `RetellWebhookEnvelope` or move the SQLAlchemy import to module top.

### L5. `_normalize_dob` ambiguous date format
**Where:** `src/app/retell/handlers.py:282-297`
**Issue:** Tries `%m/%d/%Y` then `%d/%m/%Y`. For "01/02/2000" the US form silently wins. If a non-US patient's DOB lands in the system one way and they verify by speaking it the other way, the identity gate falsely fails (or, worse, falsely passes).
**Fix:** Reject ambiguous formats — only accept ISO `YYYY-MM-DD` from Retell. Make Retell normalize before sending.

### L6. Dashboard `ix_call_dashboard_open_callbacks` partial index references string literal `'needs_callback'`
**Where:** `src/app/models/call.py:87-95`
**Issue:** Renaming `CallStatus.NEEDS_CALLBACK.value` won't update the partial index predicate. This is an untracked migration concern.
**Fix:** Document this dependency in a comment, or use a dedicated boolean column for "callback open" populated by trigger or app code.

### L7. `_extract_bearer_token` accepts case-insensitive scheme
**Where:** `src/app/api/routes/auth.py:131-139`
**Issue:** Standard, fine. Just noting that the same parsing should be consolidated with `oauth2_scheme` to avoid future drift.

---

## What's working well — do not change

- AES-256-GCM with per-record random 96-bit IV (`institution.py:99`); auth tag verified on decrypt. Standard NIST construction.
- Refresh-token replay detection with rotated-set window (`refresh_token_service.py:128-145`). Strong signal of compromise.
- Idempotency-with-claim for both webhooks (`webhooks.py:78`) and mid-call function calls (`idempotency.py:63`). Prevents duplicate bookings on retry.
- Identity-gate for full PHI reveal: DOB + (email exact OR phone last-4) (`handlers.py:300-334`). Correct that the Retell prompt is treated as advisory.
- Append-only audit log via PG trigger (`alembic/versions/20260217_0003_audit_logs_immutability.py`). Survives application bugs.
- Durable audit for mutating actions; best-effort for reads (`audit_decorator.py:33-44`). Right tradeoff.
- Scrubbed-only Retell ingest (`webhooks.py:46-69`); raw transcript fields ignored at the boundary.
- Trusted-proxy CIDR validation for `X-Forwarded-For` (`security.py:48-66`). Stops rate-limit-key forgery.
- Twilio webhook signature validation with redacted-on-failure logging (`twilio_webhooks.py:145-156`).
- TCPA/CASL compliance: STOP/START/HELP keyword detection, suppression list, auto-prepended footer (`sms_compliance.py`, `sms_privacy.py:149-167`).

---

## Suggested fix sequence

1. **Day 1:** B1 (cross-tenant fallback) → B2 (encryption key) → H1 (timing oracle).
2. **Week 1:** H3 (middleware order), H4 (soft-delete email), H8 (audit actor consistency), M1 (.env.example).
3. **Before first paying clinic:** H2, H5, H6, H7, M2, M3.
4. **Backlog:** all M and L items, prioritized by triage.

---

## Open questions for the user

- Is RLS coming back, or is application-level isolation the deliberate model? (H5)
- What's the expected onboarding flow for institution credentials? (Affects how strict B1's fix should be.)
- Do institution admins need self-serve unlock for their staff? (H7)
- Audit retention target — 6 years (HIPAA floor) or 7+ to cover the strictest provincial law you'll face?
