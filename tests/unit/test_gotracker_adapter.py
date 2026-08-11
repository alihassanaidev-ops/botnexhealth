from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from src.app.pms.gotracker.adapter import GoTrackerAdapter
from src.app.pms.gotracker.client import GoTrackerAPIError, GoTrackerClient
from src.app.pms.gotracker import mappers
from src.app.pms.models import BookingRequest


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
async def test_reschedule_appointment_patches_existing_without_type_or_end_time() -> None:
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
                "bookable_online": True,
            },
        }
    )
    adapter = _adapter(client)

    result = await adapter.create_appointment_type(
        name="Adult Recall",
        duration_minutes=60,
        descriptor_ids=["ignored"],
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
        },
    }
    assert result.id == "gt-9"
    assert result.source_metadata["provider_ids"] == ["gt-2", "gt-3"]


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
                "bookable_online": False,
            },
        }
    )
    adapter = _adapter(client)

    result = await adapter.update_appointment_type(
        "gt-9",
        duration_minutes=75,
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
            "provider_ids": ["2"],
            "operatory_ids": [],
        },
    }
    assert result.duration_minutes == 75
    assert result.source_metadata["bookable_online"] is False


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
