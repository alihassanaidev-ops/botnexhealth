from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from src.app.pms.gotracker.adapter import GoTrackerAdapter
from src.app.pms.gotracker.client import GoTrackerAPIError, GoTrackerClient
from src.app.pms.gotracker import mappers
from src.app.pms.base import SupportsWorkingWindowOverrides
from src.app.pms.models import BookingRequest, PatientCreateRequest


class FakeGoTrackerClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.responses: list[dict] = []

    async def request(self, method, path, *, params=None, json=None):
        self.calls.append(
            {"method": method, "path": path, "params": params or {}, "json": json}
        )
        return self.responses.pop(0) if self.responses else {"code": True, "data": []}

    async def close(self) -> None:
        pass


def _adapter(client: FakeGoTrackerClient | None = None) -> GoTrackerAdapter:
    return GoTrackerAdapter(
        client=client or FakeGoTrackerClient(),  # type: ignore[arg-type]
        institution=SimpleNamespace(slug="clinic"),
        location=SimpleNamespace(
            id="loc-1",
            slug="downtown",
            name="Downtown",
            address="123 Main",
            city="Toronto",
            phone="555-1111",
            timezone="America/Toronto",
            gotracker_product_key_encrypted="encrypted",
        ),
    )


def test_gotracker_adapter_supports_working_window_overrides() -> None:
    """Setup must read real work windows rather than derived bookable slots."""
    assert isinstance(_adapter(), SupportsWorkingWindowOverrides)


def test_upcoming_appointment_prefers_unambiguous_start_time() -> None:
    mapped = mappers.to_upcoming_appointment(
        {
            "AppointmentId": 1468,
            "AppointmentDate": "2026-09-02",
            "AppointmentTime": "15:20:00",
            "start_time": "2026-09-02T19:20:00.000Z",
            "ProviderId": 2,
        }
    )

    assert mapped["start_time"] == "2026-09-02T19:20:00.000Z"


@pytest.mark.asyncio
async def test_patient_search_pushes_identity_filters_to_synchronizer() -> None:
    """Patient lookup must search Tracker, not just Nexus's first contacts page."""
    client = FakeGoTrackerClient()
    client.responses.append(
        {
            "code": True,
            "data": [
                {
                    "ContactId": 603,
                    "FirstName": "Kiro",
                    "LastName": "Yt",
                    "BirthDate": "1999-08-14",
                    "Email": "kiro@gmail.com",
                    "Phone": "+12263500216",
                }
            ],
        }
    )

    patients = await _adapter(client).search_patients(
        "kiro yt",
        name="kiro yt",
        email="kiro@gmail.com",
        phone_number="2263500216",
        date_of_birth="1999-08-14",
    )

    assert [patient.id for patient in patients] == ["gt-603"]
    assert patients[0].date_of_birth == "1999-08-14"
    assert client.calls == [
        {
            "method": "GET",
            "path": "/api/patients/getAllContacts",
            "params": {
                "name": "kiro yt",
                "email": "kiro@gmail.com",
                "phone_number": "2263500216",
                "date_of_birth": "1999-08-14",
                "page": 1,
            },
            "json": None,
        }
    ]


@pytest.mark.asyncio
async def test_recall_history_sync_status_reads_consumer_history_status() -> None:
    client = FakeGoTrackerClient()
    client.responses.append(
        {
            "code": True,
            "data": {
                "appointment_history": {
                    "complete": True,
                    "completed_at": "2026-08-31T15:00:00Z",
                }
            },
        }
    )

    status = await _adapter(client).get_recall_history_sync_status()

    assert status["appointment_history"]["complete"] is True
    assert client.calls == [
        {
            "method": "GET",
            "path": "/api/appointments/history-status",
            "params": {},
            "json": None,
        }
    ]


@pytest.mark.asyncio
async def test_patient_browse_fetches_one_provider_page_only() -> None:
    client = FakeGoTrackerClient()
    client.responses.append(
        {
            "code": True,
            "data": [
                {
                    "ContactId": 603,
                    "FirstName": "Kiro",
                    "LastName": "Yt",
                    "IsActive": True,
                    "IsPatient": True,
                    "ModifiedTimeStamp": "2026-09-01T10:00:00Z",
                }
            ],
            "pagination": {
                "total": 401,
                "page_size": 200,
                "has_next_page": True,
            },
        }
    )

    page = await _adapter(client).browse_patients(
        cursor="2", name="Kiro", status="active"
    )

    assert [patient.id for patient in page.items] == ["gt-603"]
    assert page.items[0].extra == {
        "raw": {"ContactId": 603, "IsActive": True},
        "inactive": False,
        "updated_at": "2026-09-01T10:00:00Z",
    }
    assert page.total == 401
    assert page.next_cursor == "3"
    assert page.previous_cursor == "1"
    assert client.calls == [
        {
            "method": "GET",
            "path": "/api/patients/getAllContacts",
            "params": {"page": 2, "name": "Kiro", "isActive": "true"},
            "json": None,
        }
    ]


@pytest.mark.asyncio
async def test_patient_browse_excludes_explicit_non_patients() -> None:
    client = FakeGoTrackerClient()
    client.responses.append(
        {
            "data": [
                {"ContactId": 1, "FirstName": "Patient", "IsPatient": True},
                {"ContactId": 2, "FirstName": "Vendor", "IsPatient": False},
            ]
        }
    )

    page = await _adapter(client).browse_patients(status="all")

    assert [patient.id for patient in page.items] == ["gt-1"]


@pytest.mark.asyncio
async def test_patient_browse_rejects_an_unbounded_synchronizer_response() -> None:
    client = FakeGoTrackerClient()
    client.responses.append(
        {
            "code": True,
            "data": [
                {"ContactId": contact_id, "IsPatient": True}
                for contact_id in range(1, 202)
            ],
        }
    )

    with pytest.raises(GoTrackerAPIError, match="unbounded patient page"):
        await _adapter(client).browse_patients()


@pytest.mark.asyncio
async def test_working_windows_use_stable_ids_and_override_endpoints() -> None:
    client = FakeGoTrackerClient()
    client.responses.extend(
        [
            {
                "code": True,
                "data": [
                    {
                        "working_window_id": 519,
                        "WorkDate": "2026-08-28",
                        "ProviderId": 3,
                        "OperatoryId": 3,
                        "StartTime": "09:00:00",
                        "EndTime": "17:30:00",
                        "Source": "synced",
                        "appointment_type_ids": [1, 2],
                        "types_overridden": False,
                    }
                ],
            },
            {
                "code": True,
                "data": {
                    "working_window_id": 519,
                    "appointment_type_ids": [1],
                    "types_overridden": True,
                },
            },
            {
                "code": True,
                "data": {
                    "working_window_id": 519,
                    "appointment_type_ids": [1, 2],
                    "types_overridden": False,
                },
            },
        ]
    )
    adapter = _adapter(client)

    windows = await adapter.list_availabilities(
        provider_id="gt-3", start_date="2026-08-28", days=7
    )
    updated = await adapter.update_availability("gt-519", appointment_type_ids=["gt-1"])
    cleared = await adapter.clear_availability_override("gt-519")

    assert windows[0]["id"] == 519
    assert windows[0]["appointment_type_ids"] == [1, 2]
    assert windows[0]["types_overridden"] is False
    assert client.calls[0] == {
        "method": "GET",
        "path": "/api/scheduling/working_hours",
        "params": {"start_date": "2026-08-28", "days": 7, "provider_ids": "3"},
        "json": None,
    }
    assert client.calls[1] == {
        "method": "PATCH",
        "path": "/api/scheduling/working_hours/519",
        "params": {},
        "json": {"appointment_type_ids": ["1"]},
    }
    assert updated["types_overridden"] is True
    assert client.calls[2] == {
        "method": "DELETE",
        "path": "/api/scheduling/working_hours/519/override",
        "params": {},
        "json": None,
    }
    assert cleared["types_overridden"] is False


@pytest.mark.asyncio
async def test_closed_working_period_is_display_only_and_never_patchable() -> None:
    client = FakeGoTrackerClient()
    client.responses.append(
        {
            "code": True,
            "data": [
                {
                    "status": "closed",
                    "working_window_id": None,
                    "Source": "derived",
                    "WorkDate": "2026-09-01",
                    "ProviderId": 3,
                    "OperatoryId": 3,
                    "StartTime": "00:00:00",
                    "EndTime": "09:00:00",
                }
            ],
        }
    )
    adapter = _adapter(client)

    windows = await adapter.list_availabilities(
        provider_id="gt-3", start_date="2026-09-01", days=1, include_closed=True
    )

    assert client.calls[0]["params"] == {
        "start_date": "2026-09-01",
        "days": 1,
        "include_closed": "true",
        "provider_ids": "3",
    }
    assert windows[0]["status"] == "closed"
    assert windows[0]["synced"] is False
    assert windows[0]["id"] == "closed:2026-09-01:3:3:00:00:00:09:00:00"

    with pytest.raises(ValueError, match="cannot be updated"):
        await adapter.update_availability(
            windows[0]["id"], appointment_type_ids=["gt-1"]
        )

    assert len(client.calls) == 1


def test_gotracker_mappers_prefix_ids_and_preserve_source() -> None:
    patient = mappers.to_patient(
        {
            "ContactId": 415,
            "FirstName": "John",
            "LastName": "Smith",
            "Email": "john@example.com",
            "PhoneNumber": "5551112222",
        }
    )
    provider = mappers.to_provider(
        {
            "ProviderId": 2,
            "FirstName": "Ada",
            "LastName": "Lovelace",
            "appointment_types": [{"id": 9, "name": "Surgery", "minutes": 60}],
        }
    )
    operatory = mappers.to_operatory({"OperatoryId": 1, "Name": "Op 1"})

    assert patient.id == "gt-415"
    assert patient.source == "gotracker"
    assert patient.phone == "5551112222"
    assert provider.id == "gt-2"
    assert provider.name is None
    assert provider.appointment_types[0]["id"] == "gt-9"
    assert operatory.id == "gt-1"


def test_gotracker_provider_mapper_reads_provider_name_shape() -> None:
    provider = mappers.to_provider(
        {
            "ProviderId": 2,
            "ProviderName": "Dr. M. Smith",
            "ProviderCode": "061432100",
            "IsActive": True,
        }
    )

    assert provider.id == "gt-2"
    assert provider.name == "Dr. M. Smith"
    assert provider.first_name is None
    assert provider.last_name is None


def test_gotracker_appointment_type_metadata_is_prefixed_for_ui() -> None:
    appointment_type = mappers.to_appointment_type(
        {
            "id": 9,
            "name": "Surgery",
            "minutes": 90,
            "provider_ids": [2, 3],
            "operatory_ids": [4],
            "bookable_online": True,
        }
    )

    assert appointment_type.id == "gt-9"
    assert appointment_type.source_metadata == {
        "gotracker_appointment_type_id": 9,
        "provider_ids": ["gt-2", "gt-3"],
        "operatory_ids": ["gt-4"],
        "reason_ids": [],
        "bookable_online": True,
    }


@pytest.mark.asyncio
async def test_client_sends_product_key_and_unwraps_envelope() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={"code": True, "data": [{"id": 1}], "count": 1})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = GoTrackerClient(
        base_url="https://sync.example",
        product_key="product-key",
        client=http_client,
    )

    payload = await client.request(
        "GET", "/api/providers/getAllProviders", params={"page": 1}
    )

    assert payload["data"] == [{"id": 1}]
    assert seen["url"] == "https://sync.example/api/providers/getAllProviders?page=1"
    assert seen["key"] == "product-key"


@pytest.mark.asyncio
async def test_client_raises_safe_error_on_failure_envelope() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, json={"code": False, "error": ["Slot already booked"]}
        )
    )
    client = GoTrackerClient(
        base_url="https://sync.example",
        product_key="product-key",
        client=httpx.AsyncClient(transport=transport),
    )

    with pytest.raises(GoTrackerAPIError, match="Slot already booked"):
        await client.request("POST", "/api/appointments/book")


@pytest.mark.asyncio
async def test_find_available_slots_uses_documented_params() -> None:
    client = FakeGoTrackerClient()
    client.responses.append(
        {
            "code": True,
            "data": [
                {
                    "lid": 1,
                    "pid": 2,
                    "next_available_date": "2026-07-20",
                    "slots": [
                        {
                            "time": "2026-07-20T09:00:00-04:00",
                            "end_time": "2026-07-20T09:30:00-04:00",
                            "operatory_id": 4,
                        }
                    ],
                }
            ],
        }
    )
    adapter = _adapter(client)

    result = await adapter.find_available_slots(
        "2026-07-20",
        days=1,
        provider_id=["gt-2", "gt-3"],
        appointment_type_id="gt-9",
        operatory_ids=["gt-4"],
    )

    assert client.calls[0]["path"] == "/api/scheduling/available_slots"
    assert client.calls[0]["params"] == {
        "start_date": "2026-07-20",
        "days": 1,
        "provider_ids": "2,3",
        "appointment_type_id": "9",
        "operatory_ids": "4",
    }
    assert result.slots[0].provider_id == "gt-2"
    assert result.slots[0].operatory_id == "gt-4"
    assert result.next_available_date == "2026-07-20"


@pytest.mark.asyncio
async def test_find_available_slots_passes_tz_offset_when_supplied() -> None:
    client = FakeGoTrackerClient()
    adapter = _adapter(client)

    await adapter.find_available_slots(
        "2026-08-13",
        days=7,
        provider_id="gt-2",
        tz_offset="-04:00",
    )

    assert client.calls[0]["path"] == "/api/scheduling/available_slots"
    assert client.calls[0]["params"] == {
        "start_date": "2026-08-13",
        "days": 7,
        "provider_ids": "2",
        "tz_offset": "-04:00",
    }


@pytest.mark.asyncio
async def test_list_providers_reads_provider_name_payload() -> None:
    client = FakeGoTrackerClient()
    client.responses.append(
        {
            "code": True,
            "data": [
                {
                    "ProviderId": 2,
                    "ProviderName": "Dr. M. Smith",
                    "ProviderCode": "061432100",
                    "IsActive": True,
                },
                {
                    "ProviderId": 1,
                    "ProviderName": "Dr. J. Jones",
                    "ProviderCode": "061123400",
                    "IsActive": True,
                },
            ],
        }
    )
    adapter = _adapter(client)

    providers = await adapter.list_providers()

    assert client.calls[0]["path"] == "/api/providers/getAllProviders"
    assert [p.id for p in providers] == ["gt-2", "gt-1"]
    assert [p.name for p in providers] == ["Dr. M. Smith", "Dr. J. Jones"]


@pytest.mark.asyncio
async def test_list_providers_reads_nested_provider_payload() -> None:
    client = FakeGoTrackerClient()
    client.responses.append(
        {
            "code": True,
            "data": {
                "providers": [
                    {
                        "ProviderId": 3,
                        "ProviderName": "Lisa",
                        "IsActive": True,
                    }
                ]
            },
        }
    )
    adapter = _adapter(client)

    providers = await adapter.list_providers()

    assert len(providers) == 1
    assert providers[0].id == "gt-3"
    assert providers[0].name == "Lisa"


@pytest.mark.asyncio
async def test_create_patient_uses_consumer_writeback_endpoint() -> None:
    client = FakeGoTrackerClient()
    client.responses.append(
        {
            "code": True,
            "data": {
                "ContactId": 415,
                "FirstName": "Ada",
            },
        }
    )
    adapter = _adapter(client)

    result = await adapter.create_patient(
        PatientCreateRequest(
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.com",
            phone="+14165551212",
            date_of_birth="1990-12-10",
            provider_id="gt-2",
            gender="Female",
        )
    )

    assert result == {
        "success": True,
        "patient_id": "gt-415",
        "message": "Patient Ada created successfully.",
    }
    assert client.calls[0] == {
        "method": "POST",
        "path": "/api/patients/",
        "params": {},
        "json": {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "phone_number": "+14165551212",
            "date_of_birth": "1990-12-10",
            "provider_id": "2",
            "gender": "Female",
        },
    }


@pytest.mark.asyncio
async def test_get_patient_returns_only_upcoming_appointment_context() -> None:
    client = FakeGoTrackerClient()
    client.responses.append(
        {
            "code": True,
            "data": [
                {
                    "AppointmentId": 900000001,
                    "AppointmentDate": "2099-07-20T00:00:00.000Z",
                    "AppointmentTime": "09:00:00",
                    "ProviderId": 2,
                    "ProviderName": "Dr. M. Smith",
                    "ScheduleColumnId": 4,
                    "IsConfirmed": True,
                },
                {
                    "AppointmentId": 900000002,
                    "AppointmentDate": "2099-07-21T00:00:00.000Z",
                    "AppointmentTime": "10:00:00",
                    "ProviderId": 2,
                    "StatusId": 3,
                },
            ],
        }
    )
    adapter = _adapter(client)

    patient = await adapter.get_patient("gt-415", include=["upcoming_appts"])

    assert patient is not None
    assert patient.id == "gt-415"
    assert patient.first_name == ""
    assert patient.extra == {
        "upcoming_appointments": [
            {
                "id": "gt-900000001",
                "provider_id": "gt-2",
                "provider_name": "Dr. M. Smith",
                "start_time": "2099-07-20T09:00:00",
                "end_time": None,
                "location_id": None,
                "confirmed": True,
            }
        ]
    }
    assert client.calls[0]["method"] == "GET"
    assert client.calls[0]["path"] == "/api/appointments/getAllAppointments"
    assert client.calls[0]["params"] == {
        "contactId": "415",
        "from": datetime.now(ZoneInfo("America/Toronto")).date().isoformat(),
        "exclude_cancelled": "true",
        "page": 1,
    }


@pytest.mark.asyncio
async def test_book_and_cancel_use_documented_endpoints() -> None:
    client = FakeGoTrackerClient()
    client.responses.extend(
        [
            {
                "code": True,
                "data": {
                    "appointment_id": 900000001,
                    "status": "scheduled",
                    "start_time": "2026-07-20T09:00:00",
                    "provider_id": 2,
                    "patient_id": 415,
                },
            },
            {"code": True, "data": {}},
        ]
    )
    adapter = _adapter(client)

    booked = await adapter.book_appointment(
        BookingRequest(
            patient_id="gt-415",
            provider_id="gt-2",
            operatory_id="gt-1",
            appointment_type_id="gt-9",
            slot_start="2026-07-20T09:00",
            slot_end="2026-07-20T09:30",
        )
    )
    cancelled = await adapter.cancel_appointment("gt-900000001")

    assert booked.success is True
    assert booked.id == "gt-900000001"
    assert client.calls[0]["path"] == "/api/appointments/book"
    assert client.calls[0]["json"]["patient_id"] == "415"
    assert client.calls[1]["method"] == "PATCH"
    assert client.calls[1]["path"] == "/api/appointments/900000001/status"
    assert client.calls[1]["json"] == {"cancelled": True}
    assert cancelled.status == "cancelled"


@pytest.mark.asyncio
async def test_confirm_appointment_sets_confirmed_and_clears_preconfirmed() -> None:
    client = FakeGoTrackerClient()
    adapter = _adapter(client)

    result = await adapter.confirm_appointment("gt-900000001")

    assert result.success is True
    assert result.status == "confirmed"
    assert client.calls[0]["method"] == "PATCH"
    assert client.calls[0]["path"] == "/api/appointments/900000001/status"
    assert client.calls[0]["json"] == {"confirmed": True, "preconfirmed": False}


@pytest.mark.asyncio
async def test_preconfirm_appointment_only_sets_preconfirmed() -> None:
    client = FakeGoTrackerClient()
    adapter = _adapter(client)

    result = await adapter.preconfirm_appointment("gt-900000001")

    assert result.success is True
    assert result.status == "preconfirmed"
    assert client.calls[0]["method"] == "PATCH"
    assert client.calls[0]["path"] == "/api/appointments/900000001/status"
    assert client.calls[0]["json"] == {"preconfirmed": True}


@pytest.mark.asyncio
async def test_set_appointment_status_id_uses_status_endpoint() -> None:
    client = FakeGoTrackerClient()
    adapter = _adapter(client)

    result = await adapter.set_appointment_status_id("gt-900000001", status_id=5)

    assert result.success is True
    assert result.status == "status_updated"
    assert client.calls[0]["method"] == "PATCH"
    assert client.calls[0]["path"] == "/api/appointments/900000001/status"
    assert client.calls[0]["json"] == {"status_id": 5}


@pytest.mark.asyncio
async def test_update_appointment_uses_snake_case_consumer_endpoint() -> None:
    client = FakeGoTrackerClient()
    adapter = _adapter(client)

    result = await adapter.update_appointment(
        "gt-900000001",
        start_time="2026-08-12T14:30",
        duration_min=45,
        provider_id="gt-2",
        operatory_id="gt-1",
        patient_id="gt-583",
        reason="bridge prep",
    )

    assert result.success is True
    assert result.status == "appointment_updated"
    assert client.calls[0]["method"] == "PATCH"
    assert client.calls[0]["path"] == "/api/appointments/900000001"
    assert client.calls[0]["json"] == {
        "start_time": "2026-08-12T14:30",
        "duration_min": 45,
        "provider_id": "2",
        "operatory_id": "1",
        "patient_id": "583",
        "reason": "bridge prep",
    }


@pytest.mark.asyncio
async def test_reschedule_appointment_patches_existing_without_type_or_end_time() -> (
    None
):
    client = FakeGoTrackerClient()
    adapter = _adapter(client)

    result = await adapter.reschedule_appointment(
        "gt-900000001",
        BookingRequest(
            patient_id="gt-583",
            provider_id="gt-2",
            operatory_id="gt-7",
            appointment_type_id="gt-9",
            slot_start="2026-08-13T09:30:00-04:00",
            slot_end="2026-08-13T09:45:00-04:00",
            duration_min=5,
            note="bridge prep",
        ),
    )

    assert result.success is True
    assert result.status == "appointment_updated"
    assert client.calls[0]["method"] == "PATCH"
    assert client.calls[0]["path"] == "/api/appointments/900000001"
    assert client.calls[0]["json"] == {
        "start_time": "2026-08-13T09:30",
        "duration_min": 5,
        "provider_id": "2",
        "operatory_id": "7",
        "patient_id": "583",
        "reason": "bridge prep",
    }


@pytest.mark.asyncio
async def test_create_appointment_type_uses_gotracker_body() -> None:
    client = FakeGoTrackerClient()
    client.responses.append(
        {
            "code": True,
            "data": {
                "id": 9,
                "name": "Adult Recall",
                "minutes": 60,
                "provider_ids": [2, 3],
                "operatory_ids": [4],
                "reason_ids": [6],
                "bookable_online": True,
            },
        }
    )
    adapter = _adapter(client)

    result = await adapter.create_appointment_type(
        name="Adult Recall",
        duration_minutes=60,
        descriptor_ids=["6"],
        provider_ids=["gt-2", "3"],
        operatory_ids=["gt-4"],
    )

    assert client.calls[0] == {
        "method": "POST",
        "path": "/api/appointment_types",
        "params": {},
        "json": {
            "name": "Adult Recall",
            "minutes": 60,
            "bookable_online": True,
            "provider_ids": ["2", "3"],
            "operatory_ids": ["4"],
            "reason_ids": ["6"],
        },
    }
    assert result.id == "gt-9"
    assert result.source_metadata["provider_ids"] == ["gt-2", "gt-3"]
    assert result.source_metadata["reason_ids"] == ["6"]


@pytest.mark.asyncio
async def test_update_appointment_type_uses_gotracker_body() -> None:
    client = FakeGoTrackerClient()
    client.responses.append(
        {
            "code": True,
            "data": {
                "id": 9,
                "name": "Adult Recall",
                "minutes": 75,
                "provider_ids": [2],
                "operatory_ids": [],
                "reason_ids": [6],
                "bookable_online": False,
            },
        }
    )
    adapter = _adapter(client)

    result = await adapter.update_appointment_type(
        "gt-9",
        duration_minutes=75,
        descriptor_ids=["6"],
        provider_ids=["gt-2"],
        operatory_ids=[],
        bookable_online=False,
    )

    assert client.calls[0] == {
        "method": "PATCH",
        "path": "/api/appointment_types/9",
        "params": {},
        "json": {
            "minutes": 75,
            "bookable_online": False,
            "reason_ids": ["6"],
            "provider_ids": ["2"],
            "operatory_ids": [],
        },
    }
    assert result.duration_minutes == 75
    assert result.source_metadata["bookable_online"] is False
    assert result.source_metadata["reason_ids"] == ["6"]


@pytest.mark.asyncio
async def test_list_pms_descriptors_reads_gotracker_reasons() -> None:
    client = FakeGoTrackerClient()
    client.responses.append(
        {
            "code": True,
            "data": [
                {
                    "id": 6,
                    "name": "Bridge prep",
                    "code": None,
                    "minutes": 90,
                    "is_recall": False,
                    "active": True,
                }
            ],
        }
    )

    reasons = await _adapter(client).list_pms_descriptors()

    assert client.calls[0] == {
        "method": "GET",
        "path": "/api/reasons",
        "params": {},
        "json": None,
    }
    assert reasons == [
        {
            "id": 6,
            "name": "Bridge prep",
            "descriptor_type": "GoTracker Reason",
            "code": None,
            "active": True,
            "minutes": 90,
            "is_recall": False,
        }
    ]


@pytest.mark.asyncio
async def test_delete_appointment_type_uses_gotracker_endpoint() -> None:
    client = FakeGoTrackerClient()
    client.responses.append({"code": True, "data": {}})
    adapter = _adapter(client)

    await adapter.delete_appointment_type("gt-9")

    assert client.calls[0] == {
        "method": "DELETE",
        "path": "/api/appointment_types/9",
        "params": {},
        "json": None,
    }
