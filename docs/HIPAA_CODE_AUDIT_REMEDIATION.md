# HIPAA Code Audit — Remediation Status

**Audit:** `HIPAA_CODE_AUDIT_2026-08-29.md` (staging @ `c4c1783`, 2026-08-29)
**Status as of:** 2026-10-15, branch `fix/platform-completion`
**Task:** Platform completion list, Task 8A · S4 ("fix each item, or document why not").

Findings below are re-checked against the current code. Several were fixed on
`staging` after the audit and are marked **Fixed (pre-existing)**.

## High

| ID | Finding | Status | Notes |
|---|---|---|---|
| H-1 | PHI in URL query strings written to the gunicorn access log | **Partially fixed** | Access-log half is fixed: `gunicorn_conf.py` sets `access_log_format = '%({X-Request-ID}i)s "%m %U %H" %s %b'`, which logs the path only (`%U`), not the query string (`%r`). Still open: `q`/`email`/`phone_number`/`date_of_birth` remain query parameters on the patient-search route (`api/routes/universal/patients.py:348-351`) and a `search` param on several others, so identifiers still reach browser history, proxy logs and `Referer`. Moving them to a `POST` body is a breaking API + frontend change and is tracked separately. |
| H-2 | NexHealth webhooks authenticated by one platform-wide secret; tenant taken from the payload | **Fixed (pre-existing)** | `_live_signature_candidates` resolves per-subscription secrets from the DB, requires `webhook_subscription_id` + `subdomain` binding fields, and fails closed (`403`) on an unknown subscription (`nexhealth_webhooks.py:514-544`). The global secret is now only a local/test fallback. |

## Medium

| ID | Finding | Status | Notes |
|---|---|---|---|
| M-1 | Whole workflow context sent to OpenAI when `include_context` is on | **Partially fixed** | `_permitted_context` now supports a per-node `context_fields` allowlist (`llm_node_executor.py:190`). An empty allowlist still means "whole context", so the default is unchanged — the minimization only applies where a workflow explicitly sets `context_fields`. |
| M-2 | Entire merge context spread into Retell voice dynamic variables | **Open** | `voice_node_executor.py:474` still does `{**merge_context}`. Needs a named allowlist like the SMS path (`AUTOMATIC_RETELL_SMS_VARIABLES`). Deferred — touches the live voice path and needs QA (Task 7). |
| M-3 | Decrypted AI call summary returned in bulk from the list endpoint | **Open** | `calls.py:253` still serves `scrubbed_summary` inline in the list. Deferred — behaviour change to the calls screen; needs a product decision. |
| M-4 | `scrubbed_recording_url` served inline with no reveal/audit | **Open** | `calls.py:713` still returns it on `GET /{call_id}`. Deferred — needs the reveal-endpoint wiring. |
| M-5 | No uniqueness binding a NexHealth location/subdomain to one institution | **Fixed (pre-existing)** | `alembic/versions/20260904_nexhealth_mapping_security.py` adds the guard trigger + partial unique index; the model declares the constraint (`institution_location.py:51-55`). |
| M-6 | No replay window on NexHealth webhook signatures | **Fixed** | `_verify_signature` now rejects timestamps older than 300s (`nexhealth_webhooks.py`), mirroring GoTracker. Covered by a new regression test. |
| M-7 | No Content-Security-Policy | **Open** | No CSP in `middleware/security_headers.py` or the CloudFront distribution. Deferred — add a `ResponseHeadersPolicy` + API header; needs care so the SPA still loads. |
| M-8 | PHI ciphertext has no key-version marker; no rotation path | **Open** | `models/institution.py` encrypts `base64(iv‖ct‖tag)` with no key id and no AAD. Deferred — a keyring + re-encryption migration is a data-migration project, not a code tweak. |

## Low

| ID | Finding | Status | Notes |
|---|---|---|---|
| L-1 | Promised break-glass reveal workflow does not exist | Open | Operational gap; denial is correct and audited. |
| L-2 | Recordings bucket uses SSE-S3, not a CMK | Open | Infra hardening; defer to ops (Task 8B). |
| L-3 | Inbound-email tasks log raw exceptions | Open | `tasks/inbound_email.py` still logs `%s, exc`; should route through `safe_error_summary`. |
| L-4 | Retell signature verification tries three normalizations | Open | Defence-in-depth; tighten once the wire format is confirmed. |
| L-5 | CloudWatch log retention is 1 month | Open | Coupled to H-1; revisit with H-1. |
| L-6 | Bootstrap scripts log staff emails in cleartext | Open | Workforce, not PHI; minor. |

## Summary

- **Fixed:** H-2, M-5, M-6.
- **Partially fixed:** H-1 (log side), M-1 (opt-in allowlist).
- **Open and deliberately deferred with a reason:** M-2, M-3, M-4, M-7, M-8, L-1–L-6.
- **Highest-value remaining:** H-1's query-string move (P0-1 in the audit) and M-2 (voice minimization).

Deferred items are not claimed as done. Each either needs a data migration
(M-8), a frontend/API contract change (H-1), a product decision (M-3), or
touches the live voice path and must ride the Task 7 QA pass (M-2).
