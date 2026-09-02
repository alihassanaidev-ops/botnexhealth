"""Item 25 patient-communication snapshot and allow-list tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.app.api.routes.universal import patients as route
from src.app.pms.models import (
    PatientCommunicationSnapshot,
    UniversalClinicalNote,
    UniversalDocumentType,
    UniversalPatientDocument,
    UniversalPatientRecall,
    UniversalRecallType,
    UniversalTreatmentPlan,
)
from src.app.services.patient_communication import (
    PATIENT_ALERTS_POLICY_REASON,
    fetch_patient_communication,
    patient_communication_workflow_context,
    patient_recall_from_raw,
    pms_context_requirements,
)


def _unwrap(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _request():
    request = MagicMock()
    request.state.location = SimpleNamespace(id="loc-1")
    return request


def _user():
    return SimpleNamespace(
        id="user-1",
        role="LOCATION_ADMIN",
        institution_id="inst-1",
        location_id="loc-1",
    )


class _PMS:
    source = "nexhealth"

    def __init__(self) -> None:
        self.close = AsyncMock()

    async def list_clinical_notes(self, patient_id: str, *, max_items: int):
        return [
            {
                "id": 1,
                "patient_id": patient_id,
                "note": "clinical free text must not leave the adapter boundary",
                "note_type": "progress",
                "entered_at": "2026-08-01T10:00:00Z",
            }
        ]

    async def list_document_types(self, *, active: bool | None, max_items: int):
        return [{"id": 2, "name": "Medical history", "active": active}]

    async def list_patient_documents(self, patient_id: str, *, max_items: int):
        return [
            {
                "id": 3,
                "patient_id": patient_id,
                "document_type": {"id": 2, "name": "Medical history"},
                "file_name": "history.pdf",
                "download_url": "https://example.invalid/secret.pdf",
            }
        ]

    async def list_patient_recalls(
        self, *, patient_id: str | None = None, max_items: int
    ):
        return [
            {
                "id": 4,
                "patient_id": patient_id,
                "recall_id": 7,
                "date_due": "2026-08-15",
            }
        ]

    async def list_recall_types(self, *, max_items: int):
        return [{"id": 7, "name": "Hygiene", "interval_months": 6}]

    async def list_treatment_plans(self, patient_id: str, *, max_items: int):
        return [
            {
                "id": 8,
                "patient_id": patient_id,
                "status": "accepted",
                "fee": "900.00",
                "procedures": [{"code": "D2740"}],
            }
        ]


@pytest.mark.asyncio
async def test_fetch_patient_communication_minimizes_sensitive_fields() -> None:
    snapshot = await fetch_patient_communication(_PMS(), "nh-115", max_items=25)

    assert snapshot.patient_id == "nh-115"
    assert snapshot.patient_alerts_included is False
    assert snapshot.patient_alerts_policy == PATIENT_ALERTS_POLICY_REASON
    assert snapshot.clinical_notes[0].note_type == "progress"
    assert snapshot.patient_documents[0].name == "history.pdf"
    assert snapshot.treatment_plans[0].status == "accepted"

    body = snapshot.model_dump_json()
    assert "clinical free text" not in body
    assert "download_url" not in body
    assert "secret.pdf" not in body
    assert "900.00" not in body
    assert "D2740" not in body


def test_workflow_context_returns_only_allowed_flat_fields() -> None:
    snapshot = PatientCommunicationSnapshot(
        source="nexhealth",
        patient_id="nh-115",
        fetched_at="2026-09-01T00:00:00Z",
        clinical_notes=[
            UniversalClinicalNote(
                id="nh-1",
                source="nexhealth",
                patient_id="nh-115",
                title="Surgical consult",
            )
        ],
        document_types=[
            UniversalDocumentType(id="nh-2", source="nexhealth", name="Consent")
        ],
        patient_documents=[
            UniversalPatientDocument(
                id="nh-3",
                source="nexhealth",
                patient_id="nh-115",
                name="consent.pdf",
            )
        ],
        patient_recalls=[
            UniversalPatientRecall(
                id="nh-4",
                source="nexhealth",
                patient_id="nh-115",
                recall_type_id="nh-7",
                due_date="2026-08-15",
            )
        ],
        recall_types=[
            UniversalRecallType(
                id="nh-7",
                source="nexhealth",
                name="Hygiene",
                interval_months=6,
            )
        ],
        treatment_plans=[
            UniversalTreatmentPlan(
                id="nh-8",
                source="nexhealth",
                patient_id="nh-115",
                status="accepted",
            ),
            UniversalTreatmentPlan(
                id="nh-9",
                source="nexhealth",
                patient_id="nh-115",
                status="completed",
            ),
        ],
        patient_alerts_included=False,
        patient_alerts_policy=PATIENT_ALERTS_POLICY_REASON,
    )

    context = patient_communication_workflow_context(
        snapshot,
        [
            "recall_due_date",
            "recall_type_name",
            "recall_interval_months",
            "has_active_treatment_plan",
            "treatment_plan_statuses",
            "clinical_notes",
        ],
    )

    assert context == {
        "recall_due_date": "2026-08-15",
        "recall_type_name": "Hygiene",
        "recall_interval_months": 6,
        "treatment_plan_statuses": ["accepted", "completed"],
        "has_active_treatment_plan": True,
    }
    assert "clinical_notes" not in context
    assert "patient_documents" not in context


def test_pms_context_requirements_are_derived_from_allowed_fields() -> None:
    assert pms_context_requirements(
        ["recall_type_name", "has_active_treatment_plan"]
    ) == ["patient_recalls", "recall_types", "treatment_plans"]


def test_gotracker_recall_parser_accepts_launch_template_due_date_name() -> None:
    recall = patient_recall_from_raw(
        {
            "ContactId": "415",
            "RecallTypeName": "6-Month Hygiene",
            "recall_due_date": "2026-08-15",
        },
        source="gotracker",
    )

    assert recall.patient_id == "gt-415"
    assert recall.recall_type_name == "6-Month Hygiene"
    assert recall.due_date == "2026-08-15"


@pytest.mark.asyncio
async def test_patient_communication_route_uses_service_and_closes_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pms = SimpleNamespace(source="nexhealth", close=AsyncMock())
    snapshot = PatientCommunicationSnapshot(
        source="nexhealth",
        patient_id="nh-115",
        fetched_at="2026-09-01T00:00:00Z",
        patient_alerts_included=False,
        patient_alerts_policy=PATIENT_ALERTS_POLICY_REASON,
    )
    fetch = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(route, "fetch_patient_communication", fetch)

    response = await _unwrap(route.get_patient_communication)(
        request=_request(),
        current_user=_user(),
        patient_id="nh-115",
        max_items=50,
        pms=pms,
    )

    assert response is snapshot
    fetch.assert_awaited_once_with(pms, "nh-115", max_items=50)
    pms.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_patient_communication_route_hides_provider_error_phi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pms = SimpleNamespace(source="nexhealth", close=AsyncMock())
    monkeypatch.setattr(
        route,
        "fetch_patient_communication",
        AsyncMock(side_effect=RuntimeError("Jane Patient +15551234567 failed")),
    )

    with pytest.raises(HTTPException) as exc:
        await _unwrap(route.get_patient_communication)(
            request=_request(),
            current_user=_user(),
            patient_id="nh-115",
            max_items=50,
            pms=pms,
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == (
        "The practice system is temporarily unavailable. Try again shortly."
    )
    assert "Jane" not in exc.value.detail
    assert "+15551234567" not in exc.value.detail
    pms.close.assert_awaited_once()
