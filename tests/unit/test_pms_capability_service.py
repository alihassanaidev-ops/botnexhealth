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


def test_gotracker_native_capabilities_do_not_require_nexhealth_sync_status() -> None:
    session = AsyncMock()

    evaluation = asyncio.run(
        PmsCapabilityService(session).evaluate_location(
            institution=_institution(pms_type="gotracker"),
            location=_location(),
            requirements=["patient_recalls", "appointment_booking"],
        )
    )

    assert evaluation.supported is True
    assert evaluation.status == "supported"
    assert evaluation.pms_name == "GoTracker"
    session.execute.assert_not_called()


def test_gotracker_supports_derived_recall_template_context() -> None:
    session = AsyncMock()

    evaluation = asyncio.run(
        PmsCapabilityService(session).evaluate_location(
            institution=_institution(pms_type="gotracker"),
            location=_location(),
            requirements=[
                "patient_recalls",
                "recall_types",
                "treatment_plans",
                "appointment_booking",
            ],
        )
    )

    assert evaluation.supported is True
    assert evaluation.status == "supported"
    assert evaluation.pms_name == "GoTracker"
    assert evaluation.missing == []
    assert (
        evaluation.details["treatment_plans"].matched_api
        == "GoTracker derived recall row"
    )
    assert (
        evaluation.details["recall_types"].matched_api
        == "GoTracker derived recall row"
    )
    session.execute.assert_not_called()


def test_gotracker_still_blocks_generic_treatment_plan_reads() -> None:
    session = AsyncMock()

    evaluation = asyncio.run(
        PmsCapabilityService(session).evaluate_location(
            institution=_institution(pms_type="gotracker"),
            location=_location(),
            requirements=["treatment_plans"],
        )
    )

    assert evaluation.supported is False
    assert evaluation.status == "unsupported"
    assert evaluation.missing == ["treatment_plans"]
    session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Regression: staging reported every NexHealth clinic as an unknown PMS.
#
# Two independent causes, both of which look identical from the checklist:
# the matrix files were absent from the container image, and the DataSource
# label was preferred over NexHealth's own EMR identity.
# ---------------------------------------------------------------------------


def _sync_status_fields(
    *,
    sync_source_name: str | None = None,
    sync_source_type: str | None = None,
    emr_payload: dict | None = None,
):
    sync_status = MagicMock()
    sync_status.sync_source_name = sync_source_name
    sync_status.sync_source_type = sync_source_type
    sync_status.emr_payload = emr_payload
    return sync_status


def test_emr_identity_wins_over_a_free_text_datasource_label() -> None:
    """A Dentrix account labelled "Dentrix Test" is still Dentrix.

    `sync_source_name` is typed by whoever set the data source up. Staging
    carried "SD #2", "Dentrix Test" and "DataSource for Open Dental" across two
    locations of one practice; none resolve, so preferring that field by
    position made a fully supported clinic unverifiable.
    """
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_result(
            _sync_status_fields(
                sync_source_name="Dentrix Test",
                sync_source_type="DataSource",
                emr_payload={
                    "id": 7,
                    "name": "dentrix",
                    "type": "onprem",
                    "display_name": "Dentrix",
                },
            )
        )
    )

    evaluation = asyncio.run(
        PmsCapabilityService(session).evaluate_location(
            institution=_institution(),
            location=_location(),
            requirements=[
                "appointment_booking",
                "patient_recalls",
                "recall_types",
                "treatment_plans",
            ],
        )
    )

    assert evaluation.pms_name == "Dentrix"
    assert evaluation.supported is True
    assert evaluation.status == "supported"


def test_datasource_label_is_still_used_when_it_is_the_only_thing_that_resolves() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_result(
            _sync_status_fields(
                sync_source_name="Open Dental",
                sync_source_type="DataSource",
                emr_payload={"type": "onprem"},
            )
        )
    )

    evaluation = asyncio.run(
        PmsCapabilityService(session).evaluate_location(
            institution=_institution(),
            location=_location(),
            requirements=["patient_recalls"],
        )
    )

    assert evaluation.pms_name == "OpenDental"
    assert evaluation.supported is True


def test_unresolvable_row_still_names_what_it_saw() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=_result(
            _sync_status_fields(
                sync_source_name="SD #2",
                sync_source_type="DataSource",
                emr_payload=None,
            )
        )
    )

    evaluation = asyncio.run(
        PmsCapabilityService(session).evaluate_location(
            institution=_institution(),
            location=_location(),
            requirements=["patient_recalls"],
        )
    )

    assert evaluation.status == "unknown"
    assert evaluation.pms_name == "SD #2"


def test_every_shipped_matrix_loads() -> None:
    """The matrices are runtime data and must be present wherever the app runs.

    They live under `docs/`, which the container image excluded, so every
    lookup returned None and no recall campaign could launch in any deployed
    environment. An empty set now raises instead of degrading to "unknown".
    """
    from src.app.services.automation.pms_capability_service import (
        _MATRIX_DIR,
        _capability_matrices,
        _matrix_for_pms,
    )

    assert _MATRIX_DIR.is_dir(), f"capability matrices missing at {_MATRIX_DIR}"
    matrices = _capability_matrices()
    assert len(matrices) == 17
    for name in ("Dentrix", "OpenDental", "Athena", "Eaglesoft"):
        assert _matrix_for_pms(name) is not None, name


def test_container_image_ships_the_capability_matrices() -> None:
    """The image build must carry docs/Supported_API_Per_PMS_Nexhealth.

    `test_every_shipped_matrix_loads` passes from a source checkout whether or
    not the image includes them, which is why this went unnoticed until a
    deployed clinic could not launch recall. This asserts the build contract:
    .dockerignore drops `docs/`, so it has to re-admit this one directory, and
    the Dockerfile has to copy it.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    matrix_path = "docs/Supported_API_Per_PMS_Nexhealth"

    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")
    assert f"!{matrix_path}/" in dockerignore, (
        ".dockerignore excludes docs/ wholesale; it must re-admit "
        f"{matrix_path}/ or the image ships without the PMS matrices"
    )

    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert f"{matrix_path}/" in dockerfile, (
        f"Dockerfile must COPY {matrix_path}/ into the image"
    )
