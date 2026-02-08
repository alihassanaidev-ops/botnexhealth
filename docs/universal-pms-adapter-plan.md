# Universal PMS Adapter — Technical Implementation Plan

## Current State

- **40+ files** with direct PMS coupling
- NexHealth: 9 dedicated route files, full CRUD + booking
- Sikka: 1 route file (450+ lines), patient search + OAuth
- Retell handlers: 928 lines, 11 functions — only `lookup_patient` has Sikka branching, rest is NexHealth-only
- Global singleton clients + per-tenant NexHealth client cache
- No abstraction layer between routes/handlers and PMS clients

---

## Target Architecture

```
src/app/
├── pms/                          # NEW — Universal PMS layer
│   ├── models.py                 # Universal domain models
│   ├── base.py                   # PMSAdapter abstract base
│   ├── factory.py                # Adapter factory (tenant → adapter)
│   ├── nexhealth/
│   │   ├── adapter.py            # NexHealthAdapter (implements PMSAdapter)
│   │   ├── admin.py              # NH-specific setup (descriptors, availability linking)
│   │   └── mappers.py            # NH API response → Universal model
│   └── sikka/
│       ├── adapter.py            # SikkaAdapter (implements PMSAdapter)
│       ├── admin.py              # Sikka-specific setup
│       └── mappers.py            # Sikka API response → Universal model
│
├── nexhealth/                    # KEEP — Raw HTTP client (unchanged)
│   ├── client.py
│   ├── auth.py
│   ├── http_client.py
│   └── token_manager.py
│
├── sikka/                        # KEEP — Raw HTTP client (unchanged)
│   ├── client.py
│   ├── auth.py
│   ├── http_client.py
│   └── exceptions.py
│
├── api/routes/
│   ├── universal/                # NEW — PMS-agnostic endpoints
│   │   ├── patients.py           #   GET  /api/v1/patients
│   │   ├── slots.py              #   GET  /api/v1/slots
│   │   ├── appointments.py       #   POST /api/v1/appointments
│   │   ├── appointment_types.py  #   GET  /api/v1/appointment-types
│   │   ├── providers.py          #   GET  /api/v1/providers
│   │   └── operatories.py        #   GET  /api/v1/operatories
│   ├── admin/
│   │   ├── nexhealth_setup.py    # NEW — NH admin setup (descriptors, availability)
│   │   └── sikka_setup.py        # NEW — Sikka admin setup
│   ├── patients.py               # DEPRECATED — keep temporarily for backward compat
│   ├── appointments.py           # DEPRECATED
│   ├── sikka.py                  # DEPRECATED
│   └── ...                       # DEPRECATED (old NH routes)
│
├── retell/
│   └── handlers.py               # REFACTORED — uses PMSAdapter, no branching
│
└── dependencies.py               # REFACTORED — adapter factory injection
```

---

## Phase 1: Universal Models + Base Adapter (Days 1-2)

### 1.1 Create `src/app/pms/models.py`

```python
from __future__ import annotations
from datetime import date, datetime
from pydantic import BaseModel

class UniversalPatient(BaseModel):
    id: str
    source: str                          # "nexhealth" | "sikka"
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    date_of_birth: date | None = None

class UniversalProvider(BaseModel):
    id: str
    source: str
    first_name: str
    last_name: str
    specialty: str | None = None
    is_active: bool = True

class UniversalAppointmentType(BaseModel):
    id: str
    source: str
    name: str
    duration_minutes: int | None = None
    source_id: str                       # raw PMS ID for API calls
    source_metadata: dict = {}           # NH: descriptor_ids, etc.

class UniversalOperatory(BaseModel):
    id: str
    source: str
    name: str
    is_active: bool = True

class UniversalSlot(BaseModel):
    start: datetime
    end: datetime
    provider_id: str
    provider_name: str
    operatory_id: str | None = None
    operatory_name: str | None = None
    appointment_type_id: str

class BookingRequest(BaseModel):
    patient_id: str
    provider_id: str
    appointment_type_id: str
    slot_start: datetime
    operatory_id: str | None = None
    notes: str | None = None

class BookingResult(BaseModel):
    id: str
    source: str
    status: str                          # "confirmed" | "pending"
    start: datetime
    end: datetime
    patient_id: str
    provider_id: str

class PatientCreateRequest(BaseModel):
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    date_of_birth: date | None = None

class SetupStep(BaseModel):
    id: str
    label: str
    description: str
    required: bool = True
    completed: bool = False
```

### 1.2 Create `src/app/pms/base.py`

```python
from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import date
from .models import *

class PMSAdapter(ABC):
    """Core operations — every PMS adapter must implement these."""

    source: str  # "nexhealth" | "sikka"

    # --- Patients ---
    @abstractmethod
    async def search_patients(self, query: str) -> list[UniversalPatient]: ...

    @abstractmethod
    async def create_patient(self, req: PatientCreateRequest) -> UniversalPatient: ...

    # --- Appointment Types ---
    @abstractmethod
    async def list_appointment_types(self) -> list[UniversalAppointmentType]: ...

    # --- Providers ---
    @abstractmethod
    async def list_providers(self) -> list[UniversalProvider]: ...

    # --- Operatories ---
    @abstractmethod
    async def list_operatories(self) -> list[UniversalOperatory]: ...

    # --- Slots ---
    @abstractmethod
    async def get_available_slots(
        self,
        appointment_type_id: str,
        provider_id: str | None,
        start_date: date,
        end_date: date,
    ) -> list[UniversalSlot]: ...

    # --- Booking ---
    @abstractmethod
    async def book_appointment(self, req: BookingRequest) -> BookingResult: ...

    @abstractmethod
    async def cancel_appointment(self, appointment_id: str) -> bool: ...

    @abstractmethod
    async def reschedule_appointment(
        self, appointment_id: str, new_slot_start: datetime
    ) -> BookingResult: ...

    # --- Setup ---
    @abstractmethod
    async def get_setup_steps(self) -> list[SetupStep]: ...


class SupportsAppointmentTypeCreation(ABC):
    """Optional — NexHealth supports this, others may not."""

    @abstractmethod
    async def create_appointment_type(
        self, name: str, duration_minutes: int, descriptor_ids: list[str]
    ) -> UniversalAppointmentType: ...


class SupportsAvailabilityLinking(ABC):
    """Optional — NexHealth needs this for slots to work."""

    @abstractmethod
    async def list_pms_descriptors(self) -> list[dict]: ...

    @abstractmethod
    async def link_availability(
        self,
        provider_id: str,
        appointment_type_id: str,
        operatory_id: str,
        days: list[str],
        start_time: str,
        end_time: str,
    ) -> dict: ...
```

### 1.3 Create `src/app/pms/factory.py`

```python
from __future__ import annotations
from fastapi import Depends, HTTPException, Request
from src.app.models.tenant import Tenant
from src.app.pms.base import PMSAdapter

def get_adapter_for_tenant(tenant: Tenant) -> PMSAdapter:
    """Pick the right adapter based on what's configured."""
    if tenant.nexhealth_api_key:
        from src.app.pms.nexhealth.adapter import NexHealthAdapter
        return NexHealthAdapter(tenant)
    elif tenant.sikka_app_id:
        from src.app.pms.sikka.adapter import SikkaAdapter
        return SikkaAdapter(tenant)
    else:
        raise ValueError(f"No PMS configured for tenant {tenant.slug}")

async def get_tenant_pms(request: Request) -> PMSAdapter:
    """FastAPI dependency — resolves tenant from request and returns adapter."""
    tenant: Tenant | None = getattr(request.state, "tenant", None)
    if not tenant:
        raise HTTPException(status_code=400, detail="Tenant context required")
    return get_adapter_for_tenant(tenant)
```

---

## Phase 2: NexHealth Adapter (Days 3-6)

### 2.1 Create `src/app/pms/nexhealth/mappers.py`

Maps raw NexHealth API responses to universal models. Isolates all field-name
differences here.

```python
from src.app.pms.models import *

class NexHealthMappers:
    @staticmethod
    def to_patient(raw: dict) -> UniversalPatient:
        return UniversalPatient(
            id=f"nh-{raw['id']}",
            source="nexhealth",
            first_name=raw.get("first_name", ""),
            last_name=raw.get("last_name", ""),
            email=raw.get("email"),
            phone=raw.get("bio", {}).get("phone_number"),
            date_of_birth=raw.get("bio", {}).get("date_of_birth"),
        )

    @staticmethod
    def to_provider(raw: dict) -> UniversalProvider: ...

    @staticmethod
    def to_appointment_type(raw: dict) -> UniversalAppointmentType:
        return UniversalAppointmentType(
            id=f"nh-{raw['id']}",
            source="nexhealth",
            name=raw["name"],
            duration_minutes=raw.get("duration"),
            source_id=str(raw["id"]),
            source_metadata={
                "nh_appt_type_id": raw["id"],
                "descriptor_ids": [d["id"] for d in raw.get("appointment_descriptors", [])],
            },
        )

    @staticmethod
    def to_slot(raw: dict, appt_type_id: str) -> UniversalSlot: ...

    @staticmethod
    def to_booking_result(raw: dict) -> BookingResult: ...
```

### 2.2 Create `src/app/pms/nexhealth/adapter.py`

Wraps the existing `NexHealthClient` — does NOT rewrite the HTTP client.

```python
class NexHealthAdapter(PMSAdapter, SupportsAppointmentTypeCreation, SupportsAvailabilityLinking):
    source = "nexhealth"

    def __init__(self, tenant: Tenant):
        self.tenant = tenant
        self.client = self._build_client(tenant)
        self.subdomain = tenant.nexhealth_subdomain
        self.location_id = tenant.nexhealth_location_id

    async def search_patients(self, query: str) -> list[UniversalPatient]:
        raw = await self.client.get("/patients", params={
            "name": query,
            "subdomain": self.subdomain,
            "location_id": self.location_id,
        })
        return [NexHealthMappers.to_patient(p) for p in raw.get("patients", [])]

    async def get_available_slots(self, appointment_type_id, provider_id, start_date, end_date):
        nh_type_id = appointment_type_id.removeprefix("nh-")
        params = {
            "subdomain": self.subdomain,
            "location_id": self.location_id,
            "appointment_type_id": nh_type_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        if provider_id:
            params["provider_id"] = provider_id.removeprefix("nh-")
        raw = await self.client.get("/appointment_slots", params=params)
        return [NexHealthMappers.to_slot(s, appointment_type_id) for s in raw.get("slots", [])]

    async def book_appointment(self, req: BookingRequest) -> BookingResult:
        # Resolve descriptor IDs from appointment type metadata
        appt_types = await self.list_appointment_types()
        appt_type = next((t for t in appt_types if t.id == req.appointment_type_id), None)
        descriptor_ids = appt_type.source_metadata.get("descriptor_ids", []) if appt_type else []

        raw = await self.client.post("/appointments", json={
            "patient_id": req.patient_id.removeprefix("nh-"),
            "provider_id": req.provider_id.removeprefix("nh-"),
            "start_time": req.slot_start.isoformat(),
            "appointment_descriptor_ids": descriptor_ids,
            "location_id": self.location_id,
            "subdomain": self.subdomain,
            "operatory_id": req.operatory_id.removeprefix("nh-") if req.operatory_id else None,
        })
        return NexHealthMappers.to_booking_result(raw)

    # --- NexHealth-specific setup (optional capabilities) ---

    async def list_pms_descriptors(self) -> list[dict]:
        raw = await self.client.get("/appointment_descriptors", params={...})
        return raw.get("appointment_descriptors", [])

    async def create_appointment_type(self, name, duration_minutes, descriptor_ids):
        raw = await self.client.post("/appointment_types", json={...})
        return NexHealthMappers.to_appointment_type(raw)

    async def link_availability(self, provider_id, appt_type_id, operatory_id, days, start_time, end_time):
        return await self.client.post("/availabilities", json={...})

    async def get_setup_steps(self) -> list[SetupStep]:
        return [
            SetupStep(id="select_types", label="Select appointment types", description="Choose which appointment types to offer"),
            SetupStep(id="set_durations", label="Set durations", description="Set how long each appointment type takes"),
            SetupStep(id="link_operatories", label="Assign operatories", description="Link rooms/chairs to providers"),
            SetupStep(id="set_schedules", label="Set provider schedules", description="Configure provider availability"),
        ]
```

### 2.3 Create `src/app/pms/nexhealth/admin.py`

NexHealth-specific admin setup routes (descriptors, availability linking).

---

## Phase 3: Sikka Adapter (Days 7-9)

Same structure as NexHealth adapter, but implements Sikka's flow.

- `src/app/pms/sikka/adapter.py` — wraps existing `SikkaClient`
- `src/app/pms/sikka/mappers.py` — Sikka field names → universal models
- `src/app/pms/sikka/admin.py` — Sikka-specific admin setup

Key differences from NexHealth:
- Auth: App ID + Secret → request_key per practice (24hr cache)
- Requires `office_id` and `secret_key` on each request
- May not need descriptor linking or availability setup
- Different field names (patient_id vs id, firstname vs first_name)

---

## Phase 4: Universal Routes (Days 10-12)

### 4.1 Create `src/app/api/routes/universal/patients.py`

```python
from fastapi import APIRouter, Depends
from src.app.pms.base import PMSAdapter
from src.app.pms.factory import get_tenant_pms
from src.app.pms.models import UniversalPatient, PatientCreateRequest

router = APIRouter(prefix="/patients", tags=["Patients"])

@router.get("", response_model=list[UniversalPatient])
async def search_patients(
    q: str,
    pms: PMSAdapter = Depends(get_tenant_pms),
):
    return await pms.search_patients(q)

@router.post("", response_model=UniversalPatient)
async def create_patient(
    req: PatientCreateRequest,
    pms: PMSAdapter = Depends(get_tenant_pms),
):
    return await pms.create_patient(req)
```

### 4.2 Create `src/app/api/routes/universal/slots.py`

```python
@router.get("", response_model=list[UniversalSlot])
async def get_available_slots(
    appointment_type_id: str,
    start_date: date,
    end_date: date,
    provider_id: str | None = None,
    pms: PMSAdapter = Depends(get_tenant_pms),
):
    return await pms.get_available_slots(appointment_type_id, provider_id, start_date, end_date)
```

### 4.3 Same pattern for appointments, providers, operatories, appointment_types

### 4.4 Setup/capabilities endpoint

```python
# src/app/api/routes/universal/setup.py

@router.get("/capabilities")
async def get_capabilities(pms: PMSAdapter = Depends(get_tenant_pms)):
    return {
        "source": pms.source,
        "can_create_appointment_types": isinstance(pms, SupportsAppointmentTypeCreation),
        "can_link_availability": isinstance(pms, SupportsAvailabilityLinking),
        "setup_steps": await pms.get_setup_steps(),
    }
```

### 4.5 Register universal routes in `routes/__init__.py`

```python
from src.app.api.routes.universal import patients, slots, appointments, ...

# New universal routes
api_router.include_router(patients.router)
api_router.include_router(slots.router)
api_router.include_router(appointments.router)

# Keep old routes temporarily with deprecation header
api_router.include_router(old_patients.router, prefix="/nexhealth", deprecated=True)
api_router.include_router(old_sikka.router, prefix="/sikka", deprecated=True)
```

---

## Phase 5: Retell Handler Refactor (Days 13-15)

### Before (current — 928 lines with branching):

```python
async def lookup_patient(args):
    pms_provider = args.get("pms_provider", "nexhealth")
    if pms_provider == "sikka":
        client = await _get_sikka_client(tenant)
        response = await sikka_routes.list_patients(client, office_id, secret_key, ...)
        patients = response.patients
        simplified = []
        for p in patients:
            simplified.append({"id": p.get("patient_id"), "first_name": p.get("firstname"), ...})
    else:
        client = await _get_nexhealth_client(tenant)
        response = await patient_routes.list_patients(subdomain, location_id, ...)
        patients = data.get("patients", [])
        simplified = []
        for p in patients:
            simplified.append({"id": p.get("id"), "first_name": p.get("first_name"), ...})
    return {"patients": simplified}
```

### After (universal — no branching):

```python
async def lookup_patient(args):
    tenant = await get_tenant_from_call_context()
    pms = get_adapter_for_tenant(tenant)

    patients = await pms.search_patients(args.get("name", ""))
    return {
        "patients": [
            {"id": p.id, "first_name": p.first_name, "last_name": p.last_name,
             "email": p.email, "phone": p.phone}
            for p in patients[:10]
        ]
    }
```

### All 11 handlers become ~10-15 lines each instead of 50-100+.

| Handler | Before | After |
|---------|--------|-------|
| lookup_patient | 192 lines, if/else branching | ~15 lines |
| create_patient | 84 lines, NH-only | ~15 lines |
| find_appointment_slots | 80 lines, NH-only | ~15 lines |
| book_appointment | 73 lines, NH-only | ~15 lines |
| cancel_appointment | 48 lines, NH-only | ~10 lines |
| reschedule_appointment | 45 lines, NH-only | ~10 lines |
| list_appointment_types | 53 lines, NH-only | ~10 lines |
| list_providers | 88 lines, NH-only | ~10 lines |
| list_operatories | 50 lines, NH-only | ~10 lines |
| get_location_details | 49 lines, NH-only | ~10 lines |
| list_locations | 49 lines, NH-only | ~10 lines |

**Total: 928 lines → ~130 lines. All handlers instantly support every PMS.**

---

## Phase 6: Dependency Injection Refactor (Day 16)

### Update `src/app/dependencies.py`

```python
# Replace:
#   get_nexhealth_client_dependency()
#   get_sikka_client_dependency()
#
# With:
#   get_tenant_pms() from factory.py (already built in Phase 1)

# Keep raw clients for admin/debug routes only
# Adapter factory handles client creation internally
```

### Update `src/app/main.py`

```python
# Remove global singleton initialization for NexHealth/Sikka
# Adapters are created per-request from tenant config
# Per-tenant client caching moves into adapter factory
```

---

## Phase 7: Admin Setup Routes (Days 17-18)

### NexHealth admin setup: `src/app/api/routes/admin/nexhealth_setup.py`

```python
@router.get("/descriptors")
async def list_descriptors(pms = Depends(get_tenant_pms)):
    if not isinstance(pms, SupportsAvailabilityLinking):
        raise HTTPException(400, "This PMS does not use descriptors")
    return await pms.list_pms_descriptors()

@router.post("/appointment-types")
async def create_appointment_type(req: CreateApptTypeRequest, pms = Depends(get_tenant_pms)):
    if not isinstance(pms, SupportsAppointmentTypeCreation):
        raise HTTPException(400, "This PMS does not support creating appointment types")
    return await pms.create_appointment_type(req.name, req.duration_minutes, req.descriptor_ids)

@router.post("/availability")
async def link_availability(req: LinkAvailabilityRequest, pms = Depends(get_tenant_pms)):
    if not isinstance(pms, SupportsAvailabilityLinking):
        raise HTTPException(400, "This PMS does not require availability linking")
    return await pms.link_availability(...)
```

### Sikka admin setup: `src/app/api/routes/admin/sikka_setup.py`

```python
# OAuth callback (existing)
# Practice authorization (existing)
# Whatever Sikka-specific setup is needed
```

---

## Phase 8: Testing (Days 19-22)

### 8.1 Unit tests for mappers

```python
def test_nexhealth_patient_mapper():
    raw = {"id": 123, "first_name": "John", "last_name": "Doe", "email": "john@example.com"}
    patient = NexHealthMappers.to_patient(raw)
    assert patient.id == "nh-123"
    assert patient.first_name == "John"
    assert patient.source == "nexhealth"
```

### 8.2 Mock adapter for testing routes

```python
class MockPMSAdapter(PMSAdapter):
    source = "mock"
    async def search_patients(self, query):
        return [UniversalPatient(id="mock-1", source="mock", first_name="Test", ...)]
    ...
```

### 8.3 Integration tests

- Test NexHealth adapter with real API (existing test patterns)
- Test Sikka adapter with real API
- Test universal routes with mock adapter
- Test Retell handlers with mock adapter
- Test factory picks correct adapter per tenant config

---

## Migration Strategy

### Phase A: Parallel routes (no breaking changes)

```
/api/v1/nexhealth/patients     → OLD (keep working)
/api/v1/sikka/patients          → OLD (keep working)
/api/v1/patients                → NEW (universal)
```

### Phase B: Retell handlers switch to universal

- Retell handlers call adapter methods instead of route functions
- No external API change — voice agent works the same

### Phase C: Frontend switches to universal routes

- Dashboard API calls change from `/nexhealth/patients` to `/patients`
- Setup wizard added for PMS configuration

### Phase D: Deprecate old routes

- Remove `/nexhealth/*` and `/sikka/*` routes
- All traffic through universal endpoints

---

## Timeline Summary

| Phase | What | Days | Dependencies |
|-------|------|------|-------------|
| 1 | Universal models + base adapter + factory | 2 | None |
| 2 | NexHealth adapter (wraps existing client) | 4 | Phase 1 |
| 3 | Sikka adapter (wraps existing client) | 3 | Phase 1 |
| 4 | Universal routes | 3 | Phases 2-3 |
| 5 | Retell handler refactor | 3 | Phases 2-3 |
| 6 | Dependency injection cleanup | 1 | Phases 4-5 |
| 7 | Admin setup routes | 2 | Phases 2-3 |
| 8 | Testing | 4 | All |
| **Total** | | **~22 days** | |

---

## What We're NOT Changing

- Raw HTTP clients (`nexhealth/client.py`, `sikka/client.py`) — keep as-is
- Encryption layer — keep as-is
- Tenant model fields — keep existing encrypted fields
- Auth system — keep as-is (Supabase + local JWT)
- Audit logging — keep as-is (works across adapters)
- Retell webhook security — keep as-is

## What We ARE Changing

- Adding an abstraction layer BETWEEN routes/handlers and raw clients
- Unifying route endpoints
- Removing PMS branching from Retell handlers
- Making tenant PMS selection automatic based on configured credentials
