"""Tests for PMS capability matrix evaluation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.app.services.automation.pms_capability_service import PmsCapabilityService


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _institution(*, pms_type: str = "nexhealth"):
    institution = MagicMock()
    institution.id = "inst-1"
    institution.pms_type = pms_type
    return institution


def _location():
    location = MagicMock()
    location.id = "loc-1"
    return location


def _sync_status(pms_name: str):
    sync_status = MagicMock()
    sync_status.sync_source_name = pms_name
    sync_status.sync_source_type = None
    sync_status.emr_payload = {"display_name": pms_name}
    return sync_status


def test_dentrix_supports_recall_and_treatment_capabilities() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result(_sync_status("Dentrix")))

    evaluation = asyncio.run(
        PmsCapabilityService(session).evaluate_location(
            institution=_institution(),
            location=_location(),
            requirements=["patient_recalls", "treatment_plans"],
        )
    )

    assert evaluation.supported is True
    assert evaluation.status == "supported"
    assert evaluation.pms_name == "Dentrix"


def test_dentrix_ascend_blocks_treatment_plan_templates() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result(_sync_status("Dentrix Ascend")))

    evaluation = asyncio.run(
        PmsCapabilityService(session).evaluate_location(
            institution=_institution(),
            location=_location(),
            requirements=["treatment_plans"],
        )
    )

    assert evaluation.supported is False
    assert evaluation.status == "unsupported"
    assert evaluation.missing == ["treatment_plans"]


def test_unknown_pms_identity_blocks_gated_capability() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result(None))

    evaluation = asyncio.run(
        PmsCapabilityService(session).evaluate_location(
            institution=_institution(),
            location=_location(),
            requirements=["patient_recalls"],
        )
    )

    assert evaluation.supported is False
    assert evaluation.status == "unknown"
    assert evaluation.unknown == ["patient_recalls"]


def test_no_pms_institution_blocks_gated_capability() -> None:
    session = AsyncMock()

    evaluation = asyncio.run(
        PmsCapabilityService(session).evaluate_location(
            institution=_institution(pms_type="none"),
            location=_location(),
            requirements=["patient_recalls"],
        )
    )

    assert evaluation.supported is False
    assert evaluation.status == "unsupported"
    assert evaluation.missing == ["patient_recalls"]
    session.execute.assert_not_called()
