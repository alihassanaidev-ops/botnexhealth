"""Patient-communication PMS reads and workflow-safe context.

Item 25 gives workflows access to recall and treatment-plan facts, but not the
raw clinical chart. This module is the boundary: it normalizes PMS-specific
records into bounded models and exposes a small, explicit allow-list for
automation context.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import re
from typing import Any

from pydantic import BaseModel

from src.app.pms.base import PMSAdapter
from src.app.pms.models import (
    PatientCommunicationSnapshot,
    UniversalClinicalNote,
    UniversalDocumentType,
    UniversalPatientDocument,
    UniversalPatientRecall,
    UniversalRecallType,
    UniversalTreatmentPlan,
)

PATIENT_ALERTS_POLICY_REASON = (
    "Patient alerts are excluded by Decision G: NexHealth alert reads only cover "
    "alerts created through the NexHealth API, and free-text staff alerts are not "
    "safe automation inputs."
)

# GoTracker writes may remain queued after the cloud API accepts them. Patient
# messaging for these clinics therefore belongs to the workflow engine, where a
# campaign can branch explicitly on pending / written / failed outcomes. The
# older Retell post-call hooks must not send a parallel confirmation outside the
# campaign graph.
CAMPAIGN_ONLY_PATIENT_COMMUNICATION_PMS_TYPES = frozenset({"gotracker"})


def patient_communication_requires_campaign(pms_type: str | None) -> bool:
    """Whether patient-facing SMS/email must originate from a campaign.

    This does not affect staff alerts or authentication email. It only blocks
    legacy patient notifications dispatched directly by post-call processing.
    """
    return (pms_type or "").strip().lower() in (
        CAMPAIGN_ONLY_PATIENT_COMMUNICATION_PMS_TYPES
    )

RECALL_CONTEXT_FIELDS = frozenset(
    {
        "recall_due_date",
        "recall_type_id",
        "recall_type_name",
        "recall_type",
        "recall_interval_months",
        "last_visit_date",
    }
)
RECALL_TYPE_CONTEXT_FIELDS = frozenset(
    {"recall_type_id", "recall_type_name", "recall_type", "recall_interval_months"}
)
TREATMENT_PLAN_CONTEXT_FIELDS = frozenset(
    {
        "treatment_plan_statuses",
        "active_treatment_plan_count",
        "has_active_treatment_plan",
    }
)
PATIENT_COMMUNICATION_CONTEXT_FIELDS = (
    RECALL_CONTEXT_FIELDS | TREATMENT_PLAN_CONTEXT_FIELDS
)
ACTIVE_TREATMENT_PLAN_STATUSES = frozenset(
    {
        "active",
        "accepted",
        "in_progress",
        "in_treatment",
        "started",
    }
)


async def fetch_patient_communication(
    pms: PMSAdapter,
    patient_id: str,
    *,
    max_items: int = 100,
) -> PatientCommunicationSnapshot:
    """Read a bounded Item 25 snapshot from the configured PMS adapter.

    Unsupported optional families return empty lists, which keeps the universal
    endpoint honest for non-NexHealth adapters without pretending those PMSes
    implemented the family.
    """
    limit = max(1, min(int(max_items), 500))
    source = str(getattr(pms, "source", "unknown") or "unknown")

    clinical_notes = _coerce_list(
        await _read_optional(pms, "list_clinical_notes", patient_id, max_items=limit),
        UniversalClinicalNote,
        source=source,
    )
    document_types = _coerce_list(
        await _read_optional(pms, "list_document_types", active=True, max_items=limit),
        UniversalDocumentType,
        source=source,
    )
    patient_documents = _coerce_list(
        await _read_optional(
            pms, "list_patient_documents", patient_id, max_items=limit
        ),
        UniversalPatientDocument,
        source=source,
    )
    patient_recalls = [
        item
        for item in (
            _coerce_patient_recall(row, source=source)
            for row in await _read_optional(
                pms,
                "list_patient_recalls",
                patient_id=patient_id,
                max_items=limit,
            )
        )
        if item is not None
    ]
    recall_types = [
        item
        for item in (
            _coerce_recall_type(row, source=source)
            for row in await _read_optional(pms, "list_recall_types", max_items=limit)
        )
        if item is not None
    ]
    treatment_plans = [
        item
        for item in (
            _coerce_treatment_plan(row, source=source)
            for row in await _read_optional(
                pms,
                "list_treatment_plans",
                patient_id,
                max_items=limit,
            )
        )
        if item is not None
    ]

    return PatientCommunicationSnapshot(
        source=source,
        patient_id=_prefix(source, patient_id) or patient_id,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        clinical_notes=clinical_notes,
        document_types=document_types,
        patient_documents=patient_documents,
        patient_recalls=patient_recalls,
        recall_types=recall_types,
        treatment_plans=treatment_plans,
        patient_alerts_included=False,
        patient_alerts_policy=PATIENT_ALERTS_POLICY_REASON,
    )


def patient_communication_workflow_context(
    snapshot: PatientCommunicationSnapshot,
    allowed_fields: Iterable[str],
) -> dict[str, Any]:
    """Return only explicitly allowed flat fields for workflow evaluation."""
    allowed = _allowed_fields(allowed_fields)
    if not allowed:
        return {}

    context: dict[str, Any] = {}
    recall = _primary_recall(snapshot.patient_recalls)
    recall_type = _matching_recall_type(snapshot.recall_types, recall)

    if "recall_due_date" in allowed:
        context["recall_due_date"] = recall.due_date if recall else None
    if "recall_type_id" in allowed:
        context["recall_type_id"] = recall.recall_type_id if recall else None
    if "recall_type_name" in allowed:
        context["recall_type_name"] = _recall_type_name(recall, recall_type)
    if "recall_type" in allowed:
        context["recall_type"] = _recall_type_name(recall, recall_type)
    if "recall_interval_months" in allowed:
        context["recall_interval_months"] = (
            recall_type.interval_months if recall_type else None
        )
    if "last_visit_date" in allowed:
        context["last_visit_date"] = recall.last_visit_date if recall else None

    if allowed & TREATMENT_PLAN_CONTEXT_FIELDS:
        statuses = _treatment_plan_statuses(snapshot.treatment_plans)
        active_count = sum(
            1 for plan in snapshot.treatment_plans if _status_is_active(plan.status)
        )
        if "treatment_plan_statuses" in allowed:
            context["treatment_plan_statuses"] = statuses
        if "active_treatment_plan_count" in allowed:
            context["active_treatment_plan_count"] = active_count
        if "has_active_treatment_plan" in allowed:
            context["has_active_treatment_plan"] = active_count > 0

    return context


def pms_context_requirements(fields: Iterable[str]) -> list[str]:
    """Map workflow context fields to PMS capabilities needed to compute them."""
    allowed = _allowed_fields(fields)
    requirements: list[str] = []
    if allowed & RECALL_CONTEXT_FIELDS:
        requirements.append("patient_recalls")
    if allowed & RECALL_TYPE_CONTEXT_FIELDS:
        requirements.append("recall_types")
    if allowed & TREATMENT_PLAN_CONTEXT_FIELDS:
        requirements.append("treatment_plans")
    return list(dict.fromkeys(requirements))


def patient_recall_from_raw(
    raw: dict[str, Any],
    *,
    source: str = "nexhealth",
) -> UniversalPatientRecall:
    patient_id = (
        raw.get("patient_id")
        or raw.get("patientId")
        or raw.get("PatientId")
        or raw.get("contact_id")
        or raw.get("contactId")
        or raw.get("ContactId")
        or _nested(raw, "patient", "id")
        or raw.get("pid")
        or raw.get("patient")
    )
    recall = raw.get("recall_type") or raw.get("recallType") or raw.get("recall")
    recall_type_id = (
        raw.get("recall_type_id")
        or raw.get("recallTypeId")
        or raw.get("RecallTypeId")
        or raw.get("recall_id")
        or raw.get("recallId")
        or raw.get("RecallId")
        or (recall.get("id") if isinstance(recall, dict) else None)
    )
    recall_type_name = None
    if isinstance(recall, dict):
        recall_type_name = recall.get("name")
    elif isinstance(recall, str):
        recall_type_name = recall
    fallback_id = (
        f"{patient_id}:{recall_type_id}"
        if patient_id not in (None, "") and recall_type_id not in (None, "")
        else None
    )
    return UniversalPatientRecall(
        id=_prefix(source, raw.get("id") or fallback_id) or "",
        source=source,
        patient_id=_prefix(source, patient_id) or "",
        recall_type_id=_prefix(source, recall_type_id),
        recall_type_name=_string(
            raw.get("recall_type_name")
            or raw.get("recallTypeName")
            or raw.get("RecallTypeName")
            or raw.get("type")
            or raw.get("Type")
            or recall_type_name
        ),
        due_date=_string(
            raw.get("recall_due_date")
            or raw.get("recallDueDate")
            or raw.get("RecallDueDate")
            or raw.get("date_due")
            or raw.get("dateDue")
            or raw.get("DateDue")
            or raw.get("due_date")
            or raw.get("dueDate")
            or raw.get("DueDate")
            or raw.get("due")
            or raw.get("Due")
            or raw.get("next_visit_date")
            or raw.get("nextVisitDate")
            or raw.get("NextVisitDate")
        ),
        last_visit_date=_string(
            raw.get("last_visit_date")
            or raw.get("lastVisitDate")
            or raw.get("LastVisitDate")
            or raw.get("last_visit_at")
            or raw.get("lastVisitAt")
            or raw.get("LastVisitAt")
            or raw.get("last_visited_at")
            or raw.get("lastVisitedAt")
            or raw.get("LastVisitedAt")
        ),
        created_at=_string(raw.get("created_at") or raw.get("createdAt")),
        updated_at=_string(raw.get("updated_at") or raw.get("updatedAt")),
    )


def recall_type_from_raw(
    raw: dict[str, Any],
    *,
    source: str = "nexhealth",
) -> UniversalRecallType:
    return UniversalRecallType(
        id=_prefix(source, raw.get("id")) or "",
        source=source,
        name=_string(raw.get("name") or raw.get("Name") or raw.get("type")) or "",
        interval_months=_int_or_none(
            raw.get("interval_months")
            or raw.get("intervalMonths")
            or raw.get("months")
            or raw.get("default_interval_months")
            or raw.get("defaultIntervalMonths")
        ),
        active=raw.get("active") if isinstance(raw.get("active"), bool) else None,
        created_at=_string(raw.get("created_at") or raw.get("createdAt")),
        updated_at=_string(raw.get("updated_at") or raw.get("updatedAt")),
    )


def treatment_plan_from_raw(
    raw: dict[str, Any],
    *,
    source: str = "nexhealth",
) -> UniversalTreatmentPlan:
    patient_id = (
        raw.get("patient_id")
        or raw.get("patientId")
        or raw.get("PatientId")
        or raw.get("contact_id")
        or raw.get("contactId")
        or raw.get("ContactId")
        or _nested(raw, "patient", "id")
        or raw.get("pid")
        or raw.get("patient")
    )
    return UniversalTreatmentPlan(
        id=_prefix(source, raw.get("id")) or "",
        source=source,
        patient_id=_prefix(source, patient_id) or "",
        status=_string(raw.get("status") or raw.get("Status") or raw.get("state")),
        name=_string(raw.get("name") or raw.get("Name") or raw.get("title")),
        provider_id=_prefix(
            source,
            raw.get("provider_id")
            or raw.get("providerId")
            or raw.get("ProviderId")
            or _nested(raw, "provider", "id"),
        ),
        created_at=_string(raw.get("created_at") or raw.get("createdAt")),
        updated_at=_string(raw.get("updated_at") or raw.get("updatedAt")),
        accepted_at=_string(
            raw.get("accepted_at") or raw.get("acceptedAt") or raw.get("accepted_on")
        ),
        completed_at=_string(
            raw.get("completed_at")
            or raw.get("completedAt")
            or raw.get("completed_on")
        ),
    )


async def _read_optional(
    pms: PMSAdapter,
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> list[Any]:
    method = getattr(pms, method_name, None)
    if method is None:
        return []
    try:
        rows = await method(*args, **kwargs)
    except NotImplementedError:
        return []
    return rows if isinstance(rows, list) else []


def _coerce_list(
    rows: list[Any],
    model_type: type[BaseModel],
    *,
    source: str,
) -> list[Any]:
    items: list[Any] = []
    for row in rows:
        if isinstance(row, model_type):
            items.append(row)
        elif isinstance(row, dict):
            if model_type is UniversalClinicalNote:
                items.append(_clinical_note_from_raw(row, source=source))
            elif model_type is UniversalDocumentType:
                items.append(_document_type_from_raw(row, source=source))
            elif model_type is UniversalPatientDocument:
                items.append(_patient_document_from_raw(row, source=source))
    return items


def _coerce_patient_recall(row: Any, *, source: str) -> UniversalPatientRecall | None:
    if isinstance(row, UniversalPatientRecall):
        return row
    if isinstance(row, dict):
        return patient_recall_from_raw(row, source=source)
    return None


def _coerce_recall_type(row: Any, *, source: str) -> UniversalRecallType | None:
    if isinstance(row, UniversalRecallType):
        return row
    if isinstance(row, dict):
        return recall_type_from_raw(row, source=source)
    return None


def _coerce_treatment_plan(row: Any, *, source: str) -> UniversalTreatmentPlan | None:
    if isinstance(row, UniversalTreatmentPlan):
        return row
    if isinstance(row, dict):
        return treatment_plan_from_raw(row, source=source)
    return None


def _clinical_note_from_raw(
    raw: dict[str, Any], *, source: str
) -> UniversalClinicalNote:
    patient_id = (
        raw.get("patient_id")
        or _nested(raw, "patient", "id")
        or raw.get("pid")
        or raw.get("patient")
    )
    return UniversalClinicalNote(
        id=_prefix(source, raw.get("id")) or "",
        source=source,
        patient_id=_prefix(source, patient_id) or "",
        provider_id=_prefix(
            source, raw.get("provider_id") or _nested(raw, "provider", "id")
        ),
        procedure_id=_prefix(
            source, raw.get("procedure_id") or _nested(raw, "procedure", "id")
        ),
        note_type=_string(
            raw.get("note_type") or raw.get("type") or raw.get("category")
        ),
        title=_string(raw.get("title") or raw.get("name")),
        entered_at=_string(
            raw.get("entered_at")
            or raw.get("entered_on")
            or raw.get("entry_date")
            or raw.get("date")
        ),
        created_at=_string(raw.get("created_at")),
        updated_at=_string(raw.get("updated_at")),
    )


def _document_type_from_raw(
    raw: dict[str, Any], *, source: str
) -> UniversalDocumentType:
    return UniversalDocumentType(
        id=_prefix(source, raw.get("id")) or "",
        source=source,
        name=_string(raw.get("name") or raw.get("title")) or "",
        active=raw.get("active") if isinstance(raw.get("active"), bool) else None,
        created_at=_string(raw.get("created_at")),
        updated_at=_string(raw.get("updated_at")),
    )


def _patient_document_from_raw(
    raw: dict[str, Any], *, source: str
) -> UniversalPatientDocument:
    document_type = raw.get("document_type")
    return UniversalPatientDocument(
        id=_prefix(source, raw.get("id")) or "",
        source=source,
        patient_id=_prefix(
            source,
            raw.get("patient_id")
            or _nested(raw, "patient", "id")
            or raw.get("pid")
            or raw.get("patient"),
        )
        or "",
        document_type_id=_prefix(
            source,
            raw.get("document_type_id")
            or raw.get("type_id")
            or (document_type.get("id") if isinstance(document_type, dict) else None),
        ),
        document_type_name=_string(
            raw.get("document_type_name")
            or raw.get("type_name")
            or (document_type.get("name") if isinstance(document_type, dict) else None)
        ),
        name=_string(raw.get("name") or raw.get("title") or raw.get("file_name")),
        mime_type=_string(raw.get("mime_type") or raw.get("content_type")),
        created_at=_string(raw.get("created_at")),
        updated_at=_string(raw.get("updated_at")),
        uploaded_at=_string(raw.get("uploaded_at") or raw.get("created_at")),
    )


def _primary_recall(
    recalls: list[UniversalPatientRecall],
) -> UniversalPatientRecall | None:
    if not recalls:
        return None
    return sorted(recalls, key=lambda item: _date_sort_key(item.due_date))[0]


def _matching_recall_type(
    recall_types: list[UniversalRecallType],
    recall: UniversalPatientRecall | None,
) -> UniversalRecallType | None:
    if recall is None or not recall.recall_type_id:
        return None
    for item in recall_types:
        if item.id == recall.recall_type_id:
            return item
    return None


def _recall_type_name(
    recall: UniversalPatientRecall | None,
    recall_type: UniversalRecallType | None,
) -> str | None:
    if recall and recall.recall_type_name:
        return recall.recall_type_name
    if recall_type and recall_type.name:
        return recall_type.name
    return None


def _treatment_plan_statuses(plans: list[UniversalTreatmentPlan]) -> list[str]:
    statuses: list[str] = []
    seen: set[str] = set()
    for plan in plans:
        if not plan.status:
            continue
        cleaned = plan.status.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            statuses.append(cleaned)
            seen.add(key)
    return statuses


def _status_is_active(status: str | None) -> bool:
    if not status:
        return False
    normalized = re.sub(r"[^a-z0-9]+", "_", status.strip().casefold()).strip("_")
    return normalized in ACTIVE_TREATMENT_PLAN_STATUSES


def _allowed_fields(fields: Iterable[str]) -> set[str]:
    return {
        cleaned
        for field in fields
        if isinstance(field, str)
        if (cleaned := field.strip())
        if cleaned in PATIENT_COMMUNICATION_CONTEXT_FIELDS
    }


def _date_sort_key(value: str | None) -> tuple[int, datetime]:
    if not value:
        return (1, datetime.max.replace(tzinfo=timezone.utc))
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return (1, datetime.max.replace(tzinfo=timezone.utc))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (0, parsed.astimezone(timezone.utc))


def _prefix(source: str, value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    prefix = {"nexhealth": "nh", "gotracker": "gt"}.get(source)
    if not prefix or text.startswith(f"{prefix}-"):
        return text
    return f"{prefix}-{text}"


def _nested(raw: dict[str, Any], *keys: str) -> Any:
    current: Any = raw
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
