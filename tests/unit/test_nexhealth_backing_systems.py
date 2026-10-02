"""Capability of the practice system sitting *behind* a NexHealth account.

"NexHealth" is a façade over seventeen systems and five of them report no recall
data at all. A recall campaign on one of those publishes cleanly, scans nightly
and enrols nobody — indistinguishable from a clinic with no overdue patients.
"""

from __future__ import annotations

import glob
import json
import os

import pytest

from src.app.pms.nexhealth import backing_systems as bs


@pytest.mark.parametrize(
    "name", ["Athena", "DrChrono", "ModMed", "eClinicalWorks", "QDW"]
)
def test_systems_without_recall_are_refused(name: str) -> None:
    assert bs.supports("patient_recalls", name) is False
    assert bs.unavailable_reason("patient_recalls", name)


@pytest.mark.parametrize("name", ["Dentrix", "Open Dental", "Eaglesoft", "Curve"])
def test_systems_with_recall_are_allowed(name: str) -> None:
    assert bs.supports("patient_recalls", name) is True
    assert bs.unavailable_reason("patient_recalls", name) is None


def test_an_unknown_system_is_allowed_rather_than_silently_blocked() -> None:
    """We have not seen a sync_status for every account yet.

    Hiding recall from a Dentrix clinic because we had not learned its name is a
    far harder bug to notice than offering it to an Athena clinic, which fails
    loudly at the first scan.
    """
    assert bs.supports("patient_recalls", None) is True
    assert bs.supports("patient_recalls", "Some New PMS") is True


@pytest.mark.parametrize(
    "raw", ["Open Dental", "open_dental", "OpenDental", "  OPEN DENTAL  "]
)
def test_source_names_are_matched_however_they_are_spelled(raw: str) -> None:
    """NexHealth reports these as free text with inconsistent formatting."""
    assert bs.normalise_system_name(raw) == "opendental"
    assert bs.supports("patient_recalls", raw) is True


def test_treatment_plans_are_the_narrower_set() -> None:
    """Only four of the seventeen expose them, and recall suppression needs it."""
    assert bs.supports("treatment_plans", "Dentrix") is True
    assert bs.supports("treatment_plans", "Curve") is False
    assert bs.supports("treatment_plans", "Cloud9") is False


def test_the_reason_names_the_system_so_a_clinic_can_act_on_it() -> None:
    reason = bs.unavailable_reason("patient_recalls", "athena")
    assert reason is not None
    assert "Athena" in reason


def test_the_matrix_matches_nexhealths_own_support_table() -> None:
    """Guards against the transcription drifting from the source documents.

    The JSON files under `docs/Supported_API_Per_PMS_Nexhealth/` are NexHealth's
    per-integration support table; this asserts our constant still agrees with
    them, so a refreshed export surfaces as a test failure rather than as a
    campaign that never enrols anyone.
    """
    root = os.path.join("docs", "Supported_API_Per_PMS_Nexhealth")
    files = sorted(glob.glob(os.path.join(root, "*.json")))
    assert files, "capability matrix source documents are missing"

    for path in files:
        data = json.loads(open(path).read())
        apis = data.get("APIs", {})
        recall_flags = [
            value for key, value in apis.items() if "recall" in key.lower()
        ]
        if not recall_flags:
            continue

        documented = any(str(flag).strip().lower() == "yes" for flag in recall_flags)
        ours = bs.supports("patient_recalls", data.get("PMS Name"))
        assert ours == documented, (
            f"{data.get('PMS Name')}: support table says recall="
            f"{documented}, our matrix says {ours}"
        )


def test_resolve_system_name_prefers_a_recognised_candidate() -> None:
    """Staging's own field values, in the order the sync-status row offers them."""
    from src.app.pms.nexhealth.backing_systems import resolve_system_name

    assert resolve_system_name(["Dentrix", "Dentrix Test", "DataSource"]) == "Dentrix"
    assert resolve_system_name(["Dentrix Test", "dentrix", "DataSource"]) == "dentrix"
    assert (
        resolve_system_name(["DataSource for Open Dental", "opendental"]) == "opendental"
    )


def test_resolve_system_name_falls_back_to_the_first_candidate() -> None:
    from src.app.pms.nexhealth.backing_systems import resolve_system_name

    assert resolve_system_name(["SD #2", "DataSource"]) == "SD #2"
    assert resolve_system_name([]) is None
