# HIPAA Compliance Scope Document
## NexHealth Voice Agent Platform

**Document Version:** 1.0
**Date:** February 22, 2026
**Classification:** Confidential — For HIPAA Officer Review
**Prepared By:** NexHealth Voice Agent Engineering Team

---

## 1. Platform Overview

The NexHealth Voice Agent Platform is an AI-powered telephony system designed for dental and medical clinics. When a patient calls a clinic, an AI voice agent (powered by Retell AI) answers the call and can perform real-world actions on behalf of the clinic — such as looking up a patient's record, checking appointment availability, booking, cancelling, or rescheduling appointments.

The platform operates as a middleware layer between:

- **Retell AI** — the voice and AI engine that handles the actual phone call and spoken conversation
- **NexHealth** — the Practice Management System (PMS) integration layer that connects to the clinic's patient records (Dentrix, Eaglesoft, OpenDental, etc.)
- **Our Backend** — a FastAPI application that orchestrates all logic, enforces access control, stores operational metadata, and provides the clinic dashboard

Patient calls are not recorded or stored by our platform. The voice agent interacts with the caller in real time, takes an action (e.g., books an appointment in the PMS), and ends the call. A structured call log — containing metadata, not raw audio — is stored in our backend for clinic staff to review on the dashboard.

---

## 2. Scope of This Document

This document covers the following from a HIPAA compliance perspective:

1. What data our platform accesses, processes, and stores
2. How patient data is protected in transit and at rest
3. Access control and authentication architecture
4. Audit logging and monitoring
5. The clinical dashboard — how clinics view patient-related data
6. The transition from third-party call log storage to our own backend, and how HIPAA controls are maintained throughout

This document does **not** cover the internal security practices of third-party services (Retell AI, NexHealth, Supabase) beyond how our integration with them is structured.

---

## 3. Data Classification

### 3.1 Protected Health Information (PHI) — Accessed, Not Stored

Our platform accesses PHI through the NexHealth API on behalf of the clinic. This PHI is used in real time to serve the voice agent's functions and is **never persisted in our database**.

PHI that is accessed (not stored) includes:

| Data Element | Source | How It Is Used |
|---|---|---|
| Patient first and last name | NexHealth API | Identify caller, confirm identity verbally |
| Date of birth | NexHealth API | Identity verification during call |
| Phone number | NexHealth API | Caller match / callback number |
| Email address | NexHealth API | Identity hints (masked before display) |
| Home address | NexHealth API | Identity verification if required |
| Insurance information | NexHealth API | Conveyed to agent, not stored |
| Appointment history | NexHealth API | Context for reschedule/cancel actions |
| Medical alerts / notes | NexHealth API | Agent context only, not stored |

**Key principle:** PHI retrieved from NexHealth is fetched at query time, relayed to the agent or returned to the clinic dashboard, and discarded. It is not written to our database.

### 3.2 Non-PHI Operational Data — Stored in Our Backend

Our backend stores metadata that is operationally necessary but does not constitute PHI on its own:

| Data Element | Where Stored | Purpose |
|---|---|---|
| Call status (e.g., booked, cancelled, no-action) | Call log (our DB) | Dashboard display |
| Call date and time | Call log (our DB) | Dashboard display |
| Call duration | Call log (our DB) | Dashboard reporting |
| Agent used | Call log (our DB) | Operational tracking |
| Patient name and phone | Call log (our DB) | Dashboard — clinic staff reference |
| Call summary (AI-generated text) | Call log (our DB) | Dashboard — post-call review |
| NexHealth patient ID | Call log (our DB) | Link to PMS record |
| Retell call ID (hashed for logs) | Webhook event table | Idempotency, no PHI |

> **Note on Patient Name and Phone in Call Logs:** These two fields are stored in our call log table to allow clinic staff to review the dashboard without requiring a separate PMS lookup on every row. These records are scoped strictly to the clinic (tenant), encrypted in transit (TLS 1.2+), and the database is encrypted at rest. This is consistent with the clinic's own PMS storing this same data.

### 3.3 Credentials and Secrets — Stored Encrypted

All third-party API credentials (NexHealth API keys, Retell API secrets, etc.) are stored encrypted in the database using AES-256-GCM. These are not PHI but are security-critical and are handled accordingly (see Section 5.2).

---

## 4. System Architecture

### 4.1 Multi-Tenant Design

The platform is designed for multi-clinic operation. Each clinic is a **Tenant** in the system. A tenant may have multiple **Locations** (e.g., a dental group with multiple offices). Every piece of data in the system — call logs, configuration, provider cache, credentials — is scoped to a Tenant or Tenant+Location pair.

Cross-tenant access is structurally prevented: every database query is filtered by `tenant_id`, and this ID is embedded in the authenticated JWT issued at login. A clinic user cannot issue queries against another clinic's data even if they were to manipulate API requests.

### 4.2 Request Flow — Voice Agent (Real-Time Call)

```
Patient Phone Call
        │
        ▼
   Retell AI (handles audio, speech-to-text, AI reasoning)
        │
        │  Function call (e.g., "look up patient John Smith, DOB 01/01/1980")
        ▼
   Our Backend API
        │
        ├─ Verifies Retell signature (HMAC)
        ├─ Resolves Tenant from Retell Agent ID
        ├─ Authenticates against NexHealth using tenant's encrypted API key
        │
        ▼
   NexHealth API
        │
        │  Returns patient record (PHI)
        ▼
   Our Backend
        │
        ├─ Applies minimum necessary data principle (returns only needed fields)
        ├─ Masks contact info (e.g., "j***n@gmail.com", "***-***-1234")
        ├─ Writes audit log (no PHI — only hashed call ID + action type)
        │
        ▼
   Retell AI (relays result to voice agent, speaks to patient)
```

PHI is accessed from NexHealth, used in the function response, and not written to disk. The audit log records that a `READ_PATIENT` action occurred, the tenant it occurred under, and the timestamp — not the patient's name or record contents.

### 4.3 Request Flow — Dashboard (Clinic Staff Reviewing Calls)

```
Clinic Staff Browser
        │
        │  HTTPS request with JWT
        ▼
   Our Backend API
        │
        ├─ Validates JWT (HS256 signature, expiry, tenant_id claim)
        ├─ Confirms user is active and belongs to this tenant
        ├─ Queries call log table (filtered by tenant_id)
        │
        ▼
   Our Database (Supabase PostgreSQL)
        │
        │  Returns call log rows for this tenant
        ▼
   Our Backend
        │
        ├─ Applies server-side filters (date range, status, provider, location, etc.)
        ├─ Writes audit log (READ_CALL_LOG action, no PHI)
        │
        ▼
   Clinic Staff Browser (displays call log rows)
```

No call to NexHealth is made for a standard dashboard load. Stored call metadata is returned. If the staff member clicks into a specific call to view the linked patient record, a real-time NexHealth lookup is performed at that point.

---

## 5. Security Controls

### 5.1 Encryption in Transit

- All communication between clients (browser, Retell AI, NexHealth) and our backend occurs over **TLS 1.2 or higher** (enforced by Render, our hosting platform).
- HTTP Strict Transport Security (HSTS) headers are set with a 1-year max-age and `includeSubDomains`, preventing protocol downgrade attacks.
- Webhook endpoints from Retell use HMAC signature verification to ensure the request origin is authentic.

### 5.2 Encryption at Rest

**Database:** Hosted on Supabase PostgreSQL, which provides AES-256 encryption at the storage layer for all data at rest.

**Credential Fields (Application-Level Encryption):**
All third-party API keys and secrets stored in our database are additionally encrypted at the application level using **AES-256-GCM** before being written to the database. This means even if the database were compromised, credentials would remain unreadable without the encryption key.

- Algorithm: AES-256-GCM (NIST-approved authenticated encryption)
- Key size: 256-bit (32 bytes)
- IV: 96-bit randomly generated per encryption operation
- Authentication tag: 128-bit (verifies ciphertext integrity, prevents tampering)
- Key storage: Environment variable injected at runtime via the deployment platform (Render), never in source code or the database

Encrypted credential fields include: NexHealth API keys, Retell API secrets, and any per-location credential overrides.

### 5.3 Security Response Headers

Every API response includes the following HTTP security headers:

| Header | Value | Purpose |
|---|---|---|
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | Enforce HTTPS |
| `X-Content-Type-Options` | `nosniff` | Prevent MIME-type sniffing |
| `X-Frame-Options` | `DENY` | Prevent clickjacking |
| `Cache-Control` | `no-store, no-cache, must-revalidate` | Prevent PHI caching in browsers or CDNs |
| `Pragma` | `no-cache` | Legacy cache prevention |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Limit referrer data leakage |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` | Restrict unnecessary browser features |

The `Cache-Control: no-store` header is particularly important for PHI: it ensures that any patient-related data returned in API responses is never stored in browser caches, proxy caches, or CDN edge nodes.

### 5.4 CORS Policy

In production, Cross-Origin Resource Sharing (CORS) is restricted to an explicit allowlist of origins (e.g., `https://dashboard.yourclinic.com`). Wildcard origins (`*`) are structurally blocked in production — the application will refuse to start if this misconfiguration is detected. This prevents unauthorized web applications from making authenticated API requests on behalf of a logged-in user.

---

## 6. Authentication and Authorization

### 6.1 User Identity

Clinic staff authenticate via **Supabase Auth**, which manages user credentials (hashed passwords, magic links, invite flows). Our backend issues its own short-lived **JWT** after verifying the Supabase session. This JWT is used for all subsequent API requests.

JWT claims include:
- `sub`: User UUID (Supabase-issued, immutable)
- `role`: `TENANT` (clinic staff) or `ADMIN` (platform staff)
- `tenant_id`: The specific clinic this user belongs to
- `exp`: Token expiration (short-lived; 15 minutes default)

### 6.2 Tenant Isolation

The `tenant_id` claim in the JWT is the enforcement boundary for data isolation. Every database query that returns clinic-specific data includes a `WHERE tenant_id = <jwt.tenant_id>` clause. This is not an optional application-layer check — it is enforced at the query level so that even a logic error elsewhere in the application cannot leak one clinic's data to another.

Admin users (`role=ADMIN`) have `tenant_id=null` in their JWT, granting cross-tenant access that is reserved for platform engineers and support staff.

### 6.3 User Invitation and Access Control

New clinic staff accounts are created via **admin-initiated invitations** only. Staff cannot self-register. The invitation flow:

1. Platform admin issues an invite to the staff member's email address via Supabase Admin API.
2. The staff member receives an email and sets their password.
3. Their account is immediately bound to their clinic's `tenant_id`.
4. Role and access permissions are stored in Supabase's `app_metadata` (admin-write-only field, not editable by the user).

This design ensures that a clinic staff member cannot modify their own access level or tenant association.

### 6.4 Rate Limiting and Brute Force Protection

- Authentication endpoints are rate-limited to **5 requests per minute per IP address**.
- After 5 consecutive failed login attempts, the account is locked and must be unlocked by an admin.
- All login attempts (success, failure, locked, inactive account) are recorded in the audit log.

---

## 7. Audit Logging

### 7.1 Overview

An append-only audit log is maintained in the database. Every significant action that touches patient data or system configuration is recorded. Audit log entries are never deleted.

### 7.2 Audit Log Structure

Each entry records:

| Field | Description |
|---|---|
| `timestamp` | UTC timestamp of the event |
| `actor` | Who acted (e.g., RETELL_AGENT, API_CLIENT, ADMIN, SYSTEM) |
| `action` | What action was taken (see list below) |
| `target_resource` | What was accessed (e.g., `patient:nexhealth-123`) |
| `outcome` | SUCCESS, FAILURE, UNAUTHORIZED, etc. |
| `tenant_id` | Which clinic this action belongs to |
| `audit_metadata` | Contextual metadata: IP address, request ID, error details |

**PHI Exclusion:** The `audit_metadata` field explicitly must not contain PHI. Patient names, dates of birth, phone numbers, or any identifying information are never written to audit records. Only structural identifiers (hashed call IDs, NexHealth patient IDs) appear in logs.

### 7.3 Audited Actions

**Patient Operations:**
- `READ_PATIENT` — Patient record accessed (e.g., lookup during a call)
- `SEARCH_PATIENTS` — Patient search performed
- `CREATE_PATIENT` — New patient created in PMS
- `UPDATE_PATIENT` — Patient record updated

**Appointment Operations:**
- `BOOK_APPOINTMENT` — Appointment scheduled
- `CANCEL_APPOINTMENT` — Appointment cancelled
- `RESCHEDULE_APPOINTMENT` — Appointment moved
- `READ_APPOINTMENT` — Appointment details accessed

**System Operations:**
- `LOGIN` — Successful or failed login attempt (with outcome)
- `WEBHOOK_RECEIVED` — Retell webhook event received
- `TENANT_CREATE / UPDATE / DELETE` — Clinic configuration changed
- `LOCATION_CREATE / UPDATE / DELETE` — Location configuration changed
- `READ_CALL_LOG` — Clinic dashboard accessed call history

### 7.4 Audit Failure Handling

Audit log writes use a fire-and-forget pattern — an audit failure does not interrupt the primary operation. This prevents a logging issue from blocking patient-facing functionality. However, audit failures are themselves logged to the application error log for operational review.

---

## 8. The Transition to Custom Backend Call Log Storage

### 8.1 Previous State

Previously, call log metadata (call outcome, date, duration, agent used, patient reference) was stored in GoHighLevel (GHL), a third-party CRM platform. The dashboard retrieved this data by making live API calls to GHL on every page load.

### 8.2 Why We Are Moving Storage to Our Own Backend

This migration is described in full in `docs/GHL_TO_BACKEND_MIGRATION_RATIONALE.md`. The three primary operational reasons are:

1. **No server-side filtering:** GHL required fetching all records and filtering client-side, making the dashboard slow and expensive as data volume grows.
2. **API rate limits:** GHL's agency-level API quota could be exhausted by normal dashboard usage, causing a full outage for all clinics simultaneously.
3. **Per-patient record cap:** GHL has a hard limit of 1,000 records per contact with no auto-purge, meaning long-tenured patients would silently stop having new calls logged.

### 8.3 HIPAA Controls for the New Storage Model

The transition introduces structured storage of patient name and phone number (the two fields necessary to make the call log dashboard useful to clinic staff). The following controls apply:

**Data Minimization:**
We store only the minimum fields necessary for the dashboard to function without requiring a live PMS lookup on every row: patient name, patient phone, call date, call duration, call status, call summary, and agent used. We do not store date of birth, address, insurance, or any clinical information.

**Access Controls:**
Call log records are strictly scoped to the clinic's `tenant_id`. The same tenant isolation architecture described in Section 6.2 applies to call logs. A clinic user can only retrieve call logs for their own clinic.

**Encryption in Transit:**
All call log data is transmitted over TLS. No call log data is ever returned over an unencrypted connection.

**Encryption at Rest:**
Call log data resides in our Supabase PostgreSQL database, which is AES-256 encrypted at the storage layer.

**Audit Trail:**
Every access to the call log table is recorded in the audit log (`READ_CALL_LOG` action). This creates a traceable record of who accessed what call data and when.

**Retention Policy:**
Call log data retention periods will be defined in the Business Associate Agreement (BAA) between the clinic and the platform. Data deletion requests will be fulfilled by tenant-scoped DELETE operations across all call log records associated with that clinic.

**No Secondary Use:**
Call log data is used exclusively to power the clinic's own dashboard. It is never shared with other tenants, used for training AI models, or disclosed to third parties outside of what is required for platform operations.

### 8.4 Business Associate Agreement (BAA)

Because our backend stores call metadata that includes patient names and phone numbers (limited PHI), our platform qualifies as a **Business Associate** under HIPAA. A BAA must be in place with each clinic (Covered Entity) before the platform is deployed in a production environment for that clinic.

---

## 9. Hosting and Infrastructure

| Component | Provider | Relevant Security |
|---|---|---|
| Backend API | Render | SOC 2 Type II, TLS enforced, secrets via environment injection |
| Database | Supabase (PostgreSQL) | AES-256 at rest, SOC 2 Type II, no direct public access |
| Auth | Supabase Auth | OAuth2, email-based invites, app_metadata for role claims |
| Voice AI | Retell AI | HIPAA BAA available, audio not retained by our platform |
| PMS Integration | NexHealth | HIPAA BAA in place, all requests authenticated per-tenant |

---

## 10. Data Flow Summary

| Data Type | Stored Locally? | Encrypted at Rest? | Encrypted in Transit? | Access Logged? |
|---|---|---|---|---|
| Patient PHI (name, DOB, phone, address, insurance) | No | N/A | Yes (TLS) | Yes |
| Call log metadata (name, phone, status, summary) | Yes | Yes (DB-level) | Yes (TLS) | Yes |
| Third-party API credentials | Yes | Yes (AES-256-GCM) | Yes (TLS) | Yes |
| Auth tokens / JWTs | No (in-memory only) | N/A | Yes (TLS) | Yes (login events) |
| Retell call transcripts | No | N/A | N/A | N/A |
| Retell audio recordings | No | N/A | N/A | N/A |

---

## 11. Open Items and Ongoing Commitments

The following items are acknowledged as in-progress or require ongoing attention:

1. **BAA Execution:** A signed BAA must be obtained from each clinic prior to production onboarding.
2. **Penetration Testing:** A formal third-party penetration test should be conducted before general availability.
3. **Workforce Training:** All platform staff with access to production systems should complete HIPAA workforce training.
4. **Incident Response Plan:** A documented breach notification procedure must be finalized and reviewed by the HIPAA officer.
5. **Retention and Deletion Policy:** Formal data retention schedules and patient data deletion workflows to be documented and tested.
6. **Access Review:** Periodic review of which admin users have production database access, at minimum quarterly.

---

*This document is intended as a living reference for HIPAA compliance review. It should be updated whenever significant architectural changes are made to how PHI is accessed, processed, or stored.*
