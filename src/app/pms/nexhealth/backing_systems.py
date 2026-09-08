"""What the practice system *behind* a NexHealth account can actually do.

"NexHealth" is not one capability set. It is a façade over seventeen different
practice-management systems, and they differ in ways that decide whether a
campaign can work at all: **recall is impossible on Athena, DrChrono, ModMed,
eClinicalWorks and QDW**, and the treatment-plan read that gates recall
eligibility exists on only four of the seventeen.

Declaring availability as "supported on nexhealth" is therefore too coarse to be
true. A clinic on Athena would be offered a recall campaign that can never
enrol anyone — the exact failure the canonical event catalog exists to prevent,
one level further down.

The capability matrix is transcribed from
``docs/Supported_API_Per_PMS_Nexhealth/*.json``, which is NexHealth's own
per-integration support table. It is deliberately data rather than a lookup at
request time: these change on NexHealth's release schedule, not ours, and a
network call in the middle of rendering a field picker would be worse than a
stale constant.

The backing system is learned from the ``sync_status`` webhook, which reports
``sync_source_name`` — see :class:`NexHealthSyncStatus`. Until one arrives we
know nothing, and an unknown system is treated as capable: hiding a feature from
a clinic that has it is a worse failure than offering one that does not, because
the second is visible and the first is silent.
"""

from __future__ import annotations

from typing import Literal

Capability = Literal["patient_recalls", "recall_types", "treatment_plans", "clinical_notes"]

#: Backing systems that cannot report patient recalls at all.
#:
#: A recall campaign on one of these enrols nobody, ever. Keys are normalised
#: with :func:`normalise_system_name`.
NO_RECALL_SYSTEMS: frozenset[str] = frozenset(
    {
        "athena",
        "drchrono",
        "modmed",
        "eclinicalworks",
        # NexHealth reports this one as "QDW - QSI Dental Web"; the short form
        # is kept because the reported name has varied.
        "qdwqsidentalweb",
        "qdw",
    }
)

#: Backing systems that expose treatment plans. Only these can run the
#: "skip patients with active treatment" suppression the recall template uses.
TREATMENT_PLAN_SYSTEMS: frozenset[str] = frozenset(
    {"dentrix", "dentrixenterprise", "eaglesoft", "opendental"}
)

#: Human-readable names, for telling a clinic *why* something is unavailable.
DISPLAY_NAMES: dict[str, str] = {
    "athena": "Athena",
    "drchrono": "DrChrono",
    "modmed": "ModMed",
    "eclinicalworks": "eClinicalWorks",
    "qdwqsidentalweb": "QDW / QSI Dental Web",
    "qdw": "QDW / QSI Dental Web",
    "dentrix": "Dentrix",
    "dentrixenterprise": "Dentrix Enterprise",
    "eaglesoft": "Eaglesoft",
    "opendental": "Open Dental",
    "dentrixascend": "Dentrix Ascend",
    "denticon": "Denticon",
    "cloud9": "Cloud9",
    "curve": "Curve",
    "dolphin": "Dolphin",
    "nextgenoffice": "NextGen Office",
    "orthotraclocal": "Orthotrac Local",
    "practiceworks": "Practiceworks",
}


def normalise_system_name(raw: str | None) -> str | None:
    """Fold a reported source name onto a matrix key.

    NexHealth reports these as free text with inconsistent spacing, casing and
    punctuation ("Open Dental", "open_dental", "OpenDental"), so comparison is
    on alphanumerics only.
    """
    if not raw:
        return None
    key = "".join(ch for ch in raw.lower() if ch.isalnum())
    return key or None


def display_name(raw: str | None) -> str:
    key = normalise_system_name(raw)
    if key and key in DISPLAY_NAMES:
        return DISPLAY_NAMES[key]
    return (raw or "your practice software").strip()


def supports(capability: Capability, source_name: str | None) -> bool:
    """Whether the backing system supports a capability.

    Unknown systems return True. We have not yet seen a ``sync_status`` webhook
    for every account, and silently hiding recall from a Dentrix clinic because
    we had not learned its name yet would be a much harder bug to notice than
    offering it to an Athena clinic, which fails loudly at the first scan.
    """
    key = normalise_system_name(source_name)
    if key is None:
        return True

    if capability in {"patient_recalls", "recall_types"}:
        return key not in NO_RECALL_SYSTEMS
    if capability in {"treatment_plans", "clinical_notes"}:
        # Unknown systems are permitted, but a known one is only allowed if it
        # is on the short list that actually exposes these.
        return key in TREATMENT_PLAN_SYSTEMS or key not in DISPLAY_NAMES
    return True


def unavailable_reason(capability: Capability, source_name: str | None) -> str | None:
    """Why a capability is unavailable, phrased for a clinic user."""
    if supports(capability, source_name):
        return None
    label = display_name(source_name)
    if capability in {"patient_recalls", "recall_types"}:
        return f"{label} does not report patient recalls through NexHealth."
    return f"{label} does not expose treatment plans through NexHealth."


__all__ = [
    "DISPLAY_NAMES",
    "NO_RECALL_SYSTEMS",
    "TREATMENT_PLAN_SYSTEMS",
    "Capability",
    "display_name",
    "normalise_system_name",
    "supports",
    "unavailable_reason",
]
