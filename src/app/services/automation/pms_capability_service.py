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


_MATRIX_DIR = Path(__file__).resolve().parents[4] / "docs" / "Supported_API_Per_PMS_Nexhealth"
_SUPPORTED_VALUES = {"yes", "true", "supported"}
_PARTIAL_VALUES = {"partial", "limited", "read only", "read-only"}
_UNSUPPORTED_VALUES = {"no", "false", "unsupported", "not supported"}

_CAPABILITY_API_LABELS: dict[str, tuple[str, ...]] = {
    "appointments": ("View appointments", "View appointment"),
    "patients": ("View patients", "View patient"),
    "patient_recalls": ("View patient recalls", "View patient recall"),
    "procedures": ("View Procedures", "View Procedure"),
    "treatment_plans": ("View treatment plans", "View treatment plan"),
    "insurance": ("View patient insurance coverages", "View insurance plans"),
    "charges": ("View Charges", "View Charge"),
    "confirmation_writeback": ("Edit Appointment",),
    "appointment_writeback": ("Edit Appointment",),
    "appointment_booking": ("Create appointment", "View appointment slots"),
    "sync_status": ("View sync statuses",),
    "webhook_subscriptions": ("Create webhook subscription", "View webhook subscriptions"),
}


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

        pms_name = await self._resolve_pms_name(institution_id=str(institution.id), location_id=str(location.id))
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

        missing = [key for key, value in details.items() if value.status == "unsupported"]
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
            message = f"{matrix.pms_name} only partially supports: {', '.join(partial)}."
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

    async def _resolve_pms_name(self, *, institution_id: str, location_id: str) -> str | None:
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

        candidates: list[str | None] = [
            sync_status.sync_source_name,
            sync_status.sync_source_type,
        ]
        payload = sync_status.emr_payload if isinstance(sync_status.emr_payload, dict) else {}
        for key in ("display_name", "name", "type", "vendor", "pms", "software"):
            value = payload.get(key)
            if isinstance(value, str):
                candidates.append(value)

        for candidate in candidates:
            if candidate and candidate.strip():
                return candidate.strip()
        return None


def _normalize_requirements(requirements: list[str]) -> list[str]:
    return list(dict.fromkeys(req.strip() for req in requirements if req and req.strip()))


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


def _evaluate_requirement(matrix: _CapabilityMatrix, requirement: str) -> CapabilityDetail:
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
    matrices: dict[str, _CapabilityMatrix] = {}
    for path in sorted(_MATRIX_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        pms_name = str(data.get("PMS Name") or path.stem.replace("_", " "))
        apis = data.get("APIs") if isinstance(data.get("APIs"), dict) else {}
        matrices[_normalize_pms_name(pms_name)] = _CapabilityMatrix(
            pms_name=pms_name,
            apis={_normalize_label(str(key)): str(value) for key, value in apis.items()},
        )
    return matrices


def _normalize_pms_name(value: str) -> str:
    return _normalize_label(value.replace("_", " "))


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _normalize_value(value: str) -> str:
    return _normalize_label(value)
