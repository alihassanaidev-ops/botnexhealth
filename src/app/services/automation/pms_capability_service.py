"""PMS capability evaluation for NexHealth-backed campaign features."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.models.nexhealth_sync_status import NexHealthSyncStatus

CapabilityStatus = Literal["supported", "partial", "unsupported", "unknown"]


_MATRIX_DIR = (
    Path(__file__).resolve().parents[4] / "docs" / "Supported_API_Per_PMS_Nexhealth"
)
_SUPPORTED_VALUES = {"yes", "true", "supported"}
_PARTIAL_VALUES = {"partial", "limited", "read only", "read-only"}
_UNSUPPORTED_VALUES = {"no", "false", "unsupported", "not supported"}

_CAPABILITY_API_LABELS: dict[str, tuple[str, ...]] = {
    "appointments": ("View appointments", "View appointment"),
    "patients": ("View patients", "View patient"),
    "clinical_notes": ("View clinical notes", "View clinical note"),
    "document_types": ("View document types", "View document type"),
    "patient_documents": ("View patient documents", "View patient document"),
    "patient_recalls": ("View patient recalls", "View patient recall"),
    "recall_types": ("View recall types", "View recall type"),
    "procedures": ("View Procedures", "View Procedure"),
    "treatment_plans": ("View treatment plans", "View treatment plan"),
    "insurance": ("View patient insurance coverages", "View insurance plans"),
    "charges": ("View Charges", "View Charge"),
    "confirmation_writeback": ("Edit Appointment",),
    "appointment_writeback": ("Edit Appointment",),
    "appointment_booking": ("Create appointment", "View appointment slots"),
    "sync_status": ("View sync statuses",),
    "webhook_subscriptions": (
        "Create webhook subscription",
        "View webhook subscriptions",
    ),
}
_GOTRACKER_NATIVE_CAPABILITIES = frozenset(
    {
        "appointments",
        "patients",
        "patient_recalls",
        "appointment_booking",
    }
)
_GOTRACKER_RECALL_DERIVED_CAPABILITIES = frozenset(
    {
        "recall_types",
        "treatment_plans",
    }
)


@dataclass(frozen=True)
class CapabilityDetail:
    capability: str
    status: CapabilityStatus
    label: str
    matched_api: str | None = None
    raw_value: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "status": self.status,
            "label": self.label,
            "matched_api": self.matched_api,
            "raw_value": self.raw_value,
        }


@dataclass(frozen=True)
class PmsCapabilityEvaluation:
    requirements: list[str]
    supported: bool
    status: CapabilityStatus
    pms_name: str | None
    missing: list[str]
    partial: list[str]
    unknown: list[str]
    details: dict[str, CapabilityDetail]
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "requirements": self.requirements,
            "supported": self.supported,
            "status": self.status,
            "pms_name": self.pms_name,
            "missing": self.missing,
            "partial": self.partial,
            "unknown": self.unknown,
            "details": {key: value.as_dict() for key, value in self.details.items()},
            "message": self.message,
        }


@dataclass(frozen=True)
class _CapabilityMatrix:
    pms_name: str
    apis: dict[str, str]


class PmsCapabilityService:
    """Evaluates PMS support using NexHealth's supported-API matrices."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def evaluate_location(
        self,
        *,
        institution: Institution,
        location: InstitutionLocation,
        requirements: list[str],
    ) -> PmsCapabilityEvaluation:
        normalized_requirements = _normalize_requirements(requirements)
        if not normalized_requirements:
            return PmsCapabilityEvaluation(
                requirements=[],
                supported=True,
                status="supported",
                pms_name=None,
                missing=[],
                partial=[],
                unknown=[],
                details={},
                message="No PMS-specific capability is required.",
            )

        if institution.pms_type == "none":
            return _unsupported_evaluation(
                normalized_requirements,
                pms_name=None,
                message="This institution is not connected to a PMS.",
            )
        if institution.pms_type == "gotracker":
            return _gotracker_evaluation(normalized_requirements)

        pms_name = await self._resolve_pms_name(
            institution_id=str(institution.id), location_id=str(location.id)
        )
        matrix = _matrix_for_pms(pms_name)
        if matrix is None:
            return _unknown_evaluation(
                normalized_requirements,
                pms_name=pms_name,
                message=(
                    "PMS capability cannot be verified until NexHealth sync status identifies a supported PMS."
                ),
            )

        details: dict[str, CapabilityDetail] = {}
        for requirement in normalized_requirements:
            details[requirement] = _evaluate_requirement(matrix, requirement)

        missing = [
            key for key, value in details.items() if value.status == "unsupported"
        ]
        partial = [key for key, value in details.items() if value.status == "partial"]
        unknown = [key for key, value in details.items() if value.status == "unknown"]
        supported = not missing and not partial and not unknown
        status: CapabilityStatus
        if supported:
            status = "supported"
            message = f"{matrix.pms_name} supports the required PMS capabilities."
        elif missing:
            status = "unsupported"
            message = f"{matrix.pms_name} does not support: {', '.join(missing)}."
        elif partial:
            status = "partial"
            message = (
                f"{matrix.pms_name} only partially supports: {', '.join(partial)}."
            )
        else:
            status = "unknown"
            message = f"{matrix.pms_name} capability support could not be verified."

        return PmsCapabilityEvaluation(
            requirements=normalized_requirements,
            supported=supported,
            status=status,
            pms_name=matrix.pms_name,
            missing=missing,
            partial=partial,
            unknown=unknown,
            details=details,
            message=message,
        )

    async def _resolve_pms_name(
        self, *, institution_id: str, location_id: str
    ) -> str | None:
        sync_status = (
            await self.session.execute(
                select(NexHealthSyncStatus).where(
                    NexHealthSyncStatus.institution_id == institution_id,
                    NexHealthSyncStatus.location_id == location_id,
                )
            )
        ).scalar_one_or_none()
        if sync_status is None:
            return None

        return _pms_name_from_sync_status(sync_status)


def pms_name_candidates(sync_status: NexHealthSyncStatus) -> list[str]:
    """Every field on a sync-status row that could name the practice software.

    Ordered by how much the field is worth trusting. ``emr`` is NexHealth's own
    structured identity for the system behind the account
    (``{"name": "dentrix", "display_name": "Dentrix"}``), so it leads.
    ``sync_source_name`` is a free-text label somebody typed when the data
    source was set up — real values seen on staging include ``"SD #2"``,
    ``"Dentrix Test"`` and ``"DataSource for Open Dental"`` — so it can name the
    system, but it cannot be relied on to. ``sync_source_type`` is a
    discriminator (``"DataSource"``) and ``emr.type`` a hosting model
    (``"onprem"``); neither ever names a PMS, and they trail purely so an
    unresolvable row still has something to show a human.
    """
    payload = (
        sync_status.emr_payload if isinstance(sync_status.emr_payload, dict) else {}
    )

    def _emr(key: str) -> str | None:
        value = payload.get(key)
        return value if isinstance(value, str) else None

    ordered: list[str | None] = [
        _emr("display_name"),
        _emr("name"),
        sync_status.sync_source_name,
        _emr("vendor"),
        _emr("pms"),
        _emr("software"),
        sync_status.sync_source_type,
        _emr("type"),
    ]
    return [value.strip() for value in ordered if value and value.strip()]


def _pms_name_from_sync_status(sync_status: NexHealthSyncStatus) -> str | None:
    """The PMS name for a sync-status row, preferring one we hold a matrix for.

    Returning the first non-empty candidate is what made every one of these
    locations unverifiable: the DataSource label wins that race and matches no
    matrix, so a Dentrix clinic reported as ``"Dentrix Test"`` was treated as an
    unknown PMS while ``emr.display_name`` said ``"Dentrix"`` two fields later.
    Resolvability decides instead, and the first candidate is kept only as the
    label for the "cannot verify" message.
    """
    candidates = pms_name_candidates(sync_status)
    for candidate in candidates:
        if _matrix_for_pms(candidate) is not None:
            return candidate
    return candidates[0] if candidates else None


def _normalize_requirements(requirements: list[str]) -> list[str]:
    return list(
        dict.fromkeys(req.strip() for req in requirements if req and req.strip())
    )


def _unsupported_evaluation(
    requirements: list[str],
    *,
    pms_name: str | None,
    message: str,
) -> PmsCapabilityEvaluation:
    details = {
        req: CapabilityDetail(
            capability=req,
            status="unsupported",
            label=_capability_label(req),
        )
        for req in requirements
    }
    return PmsCapabilityEvaluation(
        requirements=requirements,
        supported=False,
        status="unsupported",
        pms_name=pms_name,
        missing=requirements,
        partial=[],
        unknown=[],
        details=details,
        message=message,
    )


def _unknown_evaluation(
    requirements: list[str],
    *,
    pms_name: str | None,
    message: str,
) -> PmsCapabilityEvaluation:
    details = {
        req: CapabilityDetail(
            capability=req,
            status="unknown",
            label=_capability_label(req),
        )
        for req in requirements
    }
    return PmsCapabilityEvaluation(
        requirements=requirements,
        supported=False,
        status="unknown",
        pms_name=pms_name,
        missing=[],
        partial=[],
        unknown=requirements,
        details=details,
        message=message,
    )


def _gotracker_evaluation(requirements: list[str]) -> PmsCapabilityEvaluation:
    details: dict[str, CapabilityDetail] = {}
    missing: list[str] = []
    recall_context = "patient_recalls" in requirements
    for requirement in requirements:
        supported = requirement in _GOTRACKER_NATIVE_CAPABILITIES
        derived = (
            recall_context
            and requirement in _GOTRACKER_RECALL_DERIVED_CAPABILITIES
        )
        supported = supported or derived
        if not supported:
            missing.append(requirement)
        details[requirement] = CapabilityDetail(
            capability=requirement,
            status="supported" if supported else "unsupported",
            label=_capability_label(requirement),
            matched_api=(
                "GoTracker derived recall row"
                if derived
                else "GoTracker native adapter"
                if supported
                else None
            ),
            raw_value="supported" if supported else None,
        )

    supported = not missing
    return PmsCapabilityEvaluation(
        requirements=requirements,
        supported=supported,
        status="supported" if supported else "unsupported",
        pms_name="GoTracker",
        missing=missing,
        partial=[],
        unknown=[],
        details=details,
        message=(
            "GoTracker supports the required PMS capabilities."
            if supported
            else (
                f"GoTracker does not support: {', '.join(missing)}. "
                "Generic treatment-plan reads are only supported for GoTracker "
                "when derived recall rows supply the recall context."
            )
        ),
    )


def _evaluate_requirement(
    matrix: _CapabilityMatrix, requirement: str
) -> CapabilityDetail:
    labels = _CAPABILITY_API_LABELS.get(requirement)
    if labels is None:
        return CapabilityDetail(
            capability=requirement,
            status="unknown",
            label=_capability_label(requirement),
        )

    for label in labels:
        raw_value = matrix.apis.get(_normalize_label(label))
        if raw_value is None:
            continue
        status = _status_for_value(raw_value)
        return CapabilityDetail(
            capability=requirement,
            status=status,
            label=_capability_label(requirement),
            matched_api=label,
            raw_value=raw_value,
        )

    return CapabilityDetail(
        capability=requirement,
        status="unknown",
        label=_capability_label(requirement),
    )


def _status_for_value(value: str) -> CapabilityStatus:
    normalized = _normalize_value(value)
    if normalized in _SUPPORTED_VALUES:
        return "supported"
    if normalized in _PARTIAL_VALUES:
        return "partial"
    if normalized in _UNSUPPORTED_VALUES:
        return "unsupported"
    return "unknown"


def _capability_label(capability: str) -> str:
    return capability.replace("_", " ")


def _matrix_for_pms(pms_name: str | None) -> _CapabilityMatrix | None:
    if not pms_name:
        return None
    matrices = _capability_matrices()
    key = _normalize_pms_name(pms_name)
    if key in matrices:
        return matrices[key]

    compact = key.replace(" ", "")
    for matrix_key, matrix in matrices.items():
        if compact == matrix_key.replace(" ", ""):
            return matrix
    return None


@lru_cache(maxsize=1)
def _capability_matrices() -> dict[str, _CapabilityMatrix]:
    """Load NexHealth's per-PMS supported-API tables.

    An empty result is never a legitimate state: it makes every clinic evaluate
    as "unknown PMS", which silently blocks recall from launching everywhere and
    looks identical to a genuinely unsupported practice system. That is exactly
    how this shipped broken — the tables live under ``docs/``, which the image
    excluded — so refuse to run rather than degrade into a permanent block.
    """
    matrices: dict[str, _CapabilityMatrix] = {}
    for path in sorted(_MATRIX_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        pms_name = str(data.get("PMS Name") or path.stem.replace("_", " "))
        apis = data.get("APIs") if isinstance(data.get("APIs"), dict) else {}
        matrices[_normalize_pms_name(pms_name)] = _CapabilityMatrix(
            pms_name=pms_name,
            apis={
                _normalize_label(str(key)): str(value) for key, value in apis.items()
            },
        )
    if not matrices:
        raise RuntimeError(
            "No NexHealth PMS capability matrices found at "
            f"{_MATRIX_DIR}. The deployed image must include "
            "docs/Supported_API_Per_PMS_Nexhealth/*.json — without it every "
            "clinic evaluates as an unknown PMS and no recall campaign can launch."
        )
    return matrices


def _normalize_pms_name(value: str) -> str:
    return _normalize_label(value.replace("_", " "))


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _normalize_value(value: str) -> str:
    return _normalize_label(value)
