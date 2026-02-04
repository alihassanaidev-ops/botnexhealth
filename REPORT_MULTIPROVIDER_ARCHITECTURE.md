# Multi-Tenant HIPAA-Compliant Architecture & Sikka Integration Plan

This document outlines the architectural changes, database requirements, and estimated effort to transition the current NexHealth-centric Voice Agent into a multi-tenant, HIPAA-compliant platform that supports Sikka (with GoHighLevel write-back) and NexHealth.

## 1. Architecture Overview

To support multiple practices (tenants) using different PMS providers (NexHealth vs. Sikka), we must decouple the Voice Agent logic (`handlers.py`) from specific API implementations.

### Core Components
1.  **Unified Interface (Adapter Pattern):** A standard Python interface (`PMSProvider`) that defines core actions (`get_slots`, `create_patient`, `book_appointment`).
2.  **Multi-Tenant Database:** Stores configuration, API keys (encrypted), and mapping between Retell Agents and specific Practices.
3.  **Secrets Management:** Sensitive keys (API Secrets) stored in a secure vault (e.g., AWS Secrets Manager) or encrypted columns, referenced by ID in the DB.
4.  **Audit Logging:** centralized, immutable log of all access to PHI (Patient Health Information).

---

## 2. Database Schema (Supabase/PostgreSQL)

A relational database is required to manage tenants and routing. Supabase (PostgreSQL) is a good choice if configured for HIPAA compliance (BAA required).

### Proposed Tables

#### `organizations`
*   `id` (UUID): Primary Key
*   `name`: "Smile Dental Group"
*   `created_at`

#### `practices` (Tenants)
*   `id` (UUID): Primary Key
*   `org_id` (FK): Organization
*   `name`: "Downtown Location"
*   `timezone`: "America/New_York"
*   `pms_provider`: ENUM('nexhealth', 'sikka')
*   `pms_config_id`: (FK to secure config)
*   `ghl_location_id`: GoHighLevel Location ID (for Sikka write-back)
*   `ghl_field_mapping`: JSONB - Maps standardized fields to GHL Custom Field IDs (e.g., `{"provider_requested": "contact.custom_field_123"}`)

#### `integrations_vault` (Encrypted Store)
*   `id` (UUID)
*   `practice_id` (FK)
*   `type`: 'sikka_creds', 'nexhealth_creds', 'ghl_creds'
*   `credentials`: JSONB (Encrypted) - e.g., `{ "app_id": "...", "app_key": "..." }`

#### `agents`
*   `retell_agent_id`: Primary Key (string)
*   `practice_id` (FK): Maps a Voice Agent to a specific Practice.

#### `audit_logs` (HIPAA Requirement)
*   `id`: UUID
*   `timestamp`: UTC
*   `actor`: "Retell-Agent-XYZ"
*   `action`: "READ_PATIENT"
*   `resource_id`: "patient_123"
*   `status`: "SUCCESS"
*   `metadata`: JSONB (Request ID, IP) **[NO PHI HERE]**

---

## 3. Implementation Steps

### Phase 1: Abstraction Layer (Refactoring)
**Goal:** Make `handlers.py` agnostic of Sikka or NexHealth.

1.  **Create `PMSProvider` Interface:**
    ```python
    class PMSProvider(ABC):
        async def list_patients(self, search: str) -> List[UnifiedPatient]: ...
        async def get_slots(self, start_date: date) -> List[UnifiedSlot]: ...
        async def book_appointment(self, appointment: UnifiedAppointmentRequest) -> UnifiedAppointmentResponse: ...
    ```
2.  **Create `UnifiedModels`:** Pydantic models that normalize data fields (e.g., Sikka's `firstname` vs NexHealth's `first_name`).
3.  **Implement `NexHealthAdapter`:** Move current direct calls into this class.

### Phase 2: Sikka Adapter & Logic
**Goal:** Implement Sikka specific logic, including the GHL workaround.

1.  **Read-Only Operations:**
    *   Implement `list_patients` using Sikka API.
    *   Implement `get_appointments` using Sikka API.
2.  **Slot Calculation (The Hard Part):**
    *   *Problem:* Sikka has no "Free Slots" endpoint.
    *   *Solution:*
        *   Store `OperatingHours` in the DB for the practice.
        *   Fetch all appointments for the requested day range from Sikka.
        *   Algorithm: `Slots = OperatingHours - ExistingAppointments`.
        *   *Note:* This ignores "Provider Blocks" or specific "Procedure Constraints" unless Sikka exposes them.
3.  **Write-Back (GHL):**
    *   Implement `book_appointment` in `SikkaAdapter`.
    *   Action: Instead of calling Sikka, it calls the GoHighLevel API to create a **Calendar Appointment** or **Contact Task**.
    *   Requires: `GHL_API_KEY` stored in the Vault.

### Phase 3: Infrastructure & Security
**Goal:** Deploy to a HIPAA-compliant environment.

1.  **Hosting:** AWS (EC2/Fargate) or GCP (Cloud Run) are standard. Ensure BAA (Business Associate Agreement) is signed.
2.  **Encryption:**
    *   **At Rest:** DB storage encryption (AWS RDS / Supabase).
    *   **In Transit:** TLS 1.2+ for all connections.
    *   **Application Level:** Encrypt API keys in the DB using a master key (e.g., AWS KMS).
3.  **Access Control:** Ensure the Voice Agent only retrieves data for the `practice_id` associated with the calling `agent_id`.

---

## 4. Effort Estimate

| Task Category | Specific Item | Est. Time (Hours) | Complexity |
| :--- | :--- | :--- | :--- |
| **Refactoring** | Define Abstract Interface & Unified Models | 4 | Medium |
| | Convert NexHealth Logic to Adapter | 6 | Medium |
| | Update `handlers.py` to use Factory Pattern | 4 | Low |
| **Sikka Integ.** | Implement Patient/Appointment Read | 4 | Low |
| | **Slot Calculation Logic** (Algo & Testing) | 12 | High |
| | **GHL Write-Back Integration** | 8 | Medium |
| **Database** | Setup Schema & Migrations (Supabase/SQL) | 6 | Medium |
| | Implement CRUD for Config/Tenants | 6 | Low |
| **Security** | Implement Audit Logging Middleware | 4 | Medium |
| | Secrets Encryption/Decryption Util | 6 | High |
| **Testing** | Unit & Integration Tests (Sikka & Logic) | 10 | Medium |
| **DevOps** | Infrastructure Setup (Docker/Cloud) | 8 | Medium |
| **Total** | | **~78 Hours** | |

*Note: This is roughly 2 weeks of full-time development work.*

## 5. Summary of Key Challenges

1.  **Sikka Slot Calculation:** Without a native API, calculating accurate availability is risky. If we assume a slot is open but the provider is blocked in the PMS (lunch, meeting) which Sikka doesn't report, we might double-book.
    *   *Mitigation:* sync "blocks" if available, or warn the patient that the time is "requested" rather than "confirmed".
2.  **Data Synchronization:** If we write to GHL, how does it get into Sikka?
    *   *Flow:* Voice -> GHL -> Office Staff (Manual Entry into PMS) -> Sikka (Syncs back to us).
    *   There is a delay. The Voice Agent won't see the new appointment immediately in Sikka.
3.  **Polymorphism:** `handlers.py` currently handles specific NexHealth errors/fields. The Unified Interface must hide these details so the Voice Agent doesn't crash when switching providers.
