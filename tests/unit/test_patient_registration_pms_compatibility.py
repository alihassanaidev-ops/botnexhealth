"""Registration works the same on NexHealth and GoTracker.

The registration endpoint is adapter-agnostic — it builds one
``PatientCreateRequest`` and calls ``create_patient`` on whatever adapter the
location resolves to. That only holds if both adapters accept the same request
and return the same ``{success, patient_id, message}`` contract, so this drives
each one directly and asserts they agree.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.pms.gotracker.adapter import GoTrackerAdapter
from src.app.pms.models import PatientCreateRequest
from src.app.pms.nexhealth.adapter import NexHealthAdapter

def request_for(provider_id: str) -> PatientCreateRequest:
    """One shape of request; only the provider id differs by PMS.

    NexHealth models provider_id as an integer, so it accepts "123" or the
    prefixed "nh-123" that strips to it. GoTracker takes its id as a string.
    That difference is the one thing a clinic has to get right per PMS when
    configuring a Register Patient step.
    """
    return PatientCreateRequest(
        first_name="Dana",
        last_name="Reyes",
        email="dana@example.com",
        phone="+15550001111",
        date_of_birth="1988-04-02",
        provider_id=provider_id,
        gender="Female",
    )


NH_REQUEST = request_for("nh-77")
GT_REQUEST = request_for("gt-prov-7")


def _nexhealth_adapter():
    adapter = NexHealthAdapter.__new__(NexHealthAdapter)
    adapter._client = MagicMock()
    adapter._default_params = MagicMock(return_value={"subdomain": "clinic"})
    return adapter


def _gotracker_adapter(response=None, error=None):
    adapter = GoTrackerAdapter.__new__(GoTrackerAdapter)
    client = MagicMock()
    client.request = AsyncMock(
        return_value=response, side_effect=error if error else None
    )
    adapter._client = client
    return adapter


@pytest.mark.asyncio
class TestBothAdaptersAcceptTheSameRequest:
    async def test_nexhealth_creates_and_returns_a_prefixed_id(self):
        adapter = _nexhealth_adapter()
        with patch(
            "src.app.pms.nexhealth.adapter.handle_nexhealth_request",
            AsyncMock(return_value={"code": True, "data": {"user": {"id": 4242, "first_name": "Dana"}}}),
        ):
            result = await adapter.create_patient(NH_REQUEST)

        assert result["success"] is True
        assert result["patient_id"] == "nh-4242"

    async def test_gotracker_creates_and_returns_a_prefixed_id(self):
        adapter = _gotracker_adapter(
            response={"data": {"ContactId": 991, "FirstName": "Dana"}}
        )
        result = await adapter.create_patient(GT_REQUEST)

        assert result["success"] is True
        assert result["patient_id"] == "gt-991"

    async def test_both_return_the_same_three_keys(self):
        """The endpoint reads success/patient_id and nothing adapter-specific."""
        nh = _nexhealth_adapter()
        with patch(
            "src.app.pms.nexhealth.adapter.handle_nexhealth_request",
            AsyncMock(return_value={"code": True, "data": {"user": {"id": 1}}}),
        ):
            nh_result = await nh.create_patient(NH_REQUEST)
        gt_result = await _gotracker_adapter(
            response={"data": {"ContactId": 2}}
        ).create_patient(GT_REQUEST)

        assert set(nh_result) == set(gt_result) == {"success", "patient_id", "message"}


@pytest.mark.asyncio
class TestBothAdaptersForwardEveryRequiredField:
    async def test_nexhealth_sends_dob_gender_and_provider(self):
        """The three fields the form exists to collect must reach the PMS."""
        adapter = _nexhealth_adapter()
        sender = AsyncMock(return_value={"code": True, "data": {"user": {"id": 1}}})
        with patch("src.app.pms.nexhealth.adapter.handle_nexhealth_request", sender):
            await adapter.create_patient(NH_REQUEST)

        body = sender.await_args.kwargs["json"]
        bio = body["patient"]["bio"]
        assert bio["date_of_birth"] == "1988-04-02"
        assert bio["gender"] == "Female"
        assert body["provider"]["provider_id"] == 77

    async def test_gotracker_sends_dob_gender_and_provider(self):
        adapter = _gotracker_adapter(response={"data": {"ContactId": 2}})
        await adapter.create_patient(GT_REQUEST)

        body = adapter._client.request.await_args.kwargs["json"]
        assert body["date_of_birth"] == "1988-04-02"
        assert body["gender"] == "Female"
        assert body["provider_id"] == "gt-prov-7" or body["provider_id"] == "prov-7"


@pytest.mark.asyncio
class TestFailuresLookTheSameToTheEndpoint:
    async def test_gotracker_reports_a_refusal_without_raising(self):
        """It converts its own API error into the shared contract, so the
        endpoint's 502 path covers it."""
        from src.app.pms.gotracker.client import GoTrackerAPIError

        adapter = _gotracker_adapter(error=GoTrackerAPIError("duplicate patient"))
        result = await adapter.create_patient(GT_REQUEST)

        assert result["success"] is False
        assert result["patient_id"] is None

    async def test_gotracker_reports_a_missing_id_as_failure(self):
        """A 200 with no id must not read as success and link a null."""
        adapter = _gotracker_adapter(response={"data": {}})
        result = await adapter.create_patient(GT_REQUEST)

        assert result["success"] is False
        assert result["patient_id"] is None

    async def test_nexhealth_reports_a_missing_id_as_failure(self):
        adapter = _nexhealth_adapter()
        with patch(
            "src.app.pms.nexhealth.adapter.handle_nexhealth_request",
            AsyncMock(return_value={"code": False, "error": "rejected", "data": {}}),
        ):
            result = await adapter.create_patient(NH_REQUEST)

        assert result["success"] is False
        assert result["patient_id"] is None


@pytest.mark.asyncio
class TestTheProviderIdDifference:
    """The one per-PMS thing a clinic must get right when configuring the step."""

    async def test_nexhealth_rejects_a_non_numeric_provider_id(self):
        """It models the id as an integer, so free text cannot reach the API.

        The registration endpoint turns this into a 503 for the patient and logs
        it. Worth knowing when a clinic reports "registration never works": the
        provider id on the Register Patient step is the first thing to check.
        """
        adapter = _nexhealth_adapter()
        with patch(
            "src.app.pms.nexhealth.adapter.handle_nexhealth_request", AsyncMock()
        ):
            with pytest.raises(Exception) as excinfo:
                await adapter.create_patient(request_for("dr-smith"))
        assert "provider_id" in str(excinfo.value)

    async def test_gotracker_accepts_a_string_provider_id(self):
        adapter = _gotracker_adapter(response={"data": {"ContactId": 5}})
        result = await adapter.create_patient(request_for("dr-smith"))
        assert result["success"] is True
