"""NexHealth API contract selection and derived request details."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class NexHealthAPIContract(StrEnum):
    """Normalized NexHealth API contract targets.

    Keep the public configuration aliases out at the edge; internal callers use
    these two values so headers and renamed routes cannot drift independently.
    """

    LEGACY_V2 = "legacy_v2"
    STABLE_V3 = "stable_v3"

    @property
    def api_version_header(self) -> str:
        if self is NexHealthAPIContract.LEGACY_V2:
            return "v2"
        return "v3.0.0"

    @property
    def accept_header(self) -> str:
        if self is NexHealthAPIContract.LEGACY_V2:
            return "application/vnd.Nexhealth+json;version=2"
        return "application/json"

    @property
    def slot_search_path(self) -> str:
        if self is NexHealthAPIContract.LEGACY_V2:
            return "/appointment_slots"
        return "/available_slots"

    @property
    def working_windows_path(self) -> str:
        if self is NexHealthAPIContract.LEGACY_V2:
            return "/availabilities"
        return "/working_hours"

    @property
    def working_window_wrapper_key(self) -> str:
        if self is NexHealthAPIContract.LEGACY_V2:
            return "availability"
        return "working_hour"

    def request_headers(self, *, authorization: str) -> dict[str, str]:
        return {
            "Accept": self.accept_header,
            "Authorization": authorization,
            "Nex-Api-Version": self.api_version_header,
        }


_CONTRACT_ALIASES = {
    "v2": NexHealthAPIContract.LEGACY_V2,
    "v2.2.2": NexHealthAPIContract.LEGACY_V2,
    "legacy_v2": NexHealthAPIContract.LEGACY_V2,
    "v3": NexHealthAPIContract.STABLE_V3,
    "v3.0.0": NexHealthAPIContract.STABLE_V3,
    "v20240412": NexHealthAPIContract.STABLE_V3,
    "stable_v3": NexHealthAPIContract.STABLE_V3,
}


def normalize_nexhealth_api_contract(raw: str | NexHealthAPIContract | None) -> NexHealthAPIContract:
    """Normalize public config labels to an internal contract target."""
    if isinstance(raw, NexHealthAPIContract):
        return raw

    value = str(raw or "").strip().lower().replace("-", "_")
    if value in _CONTRACT_ALIASES:
        return _CONTRACT_ALIASES[value]

    allowed = ", ".join(sorted(_CONTRACT_ALIASES))
    raise ValueError(
        "Unsupported NexHealth API version "
        f"{raw!r}. Expected one of: {allowed}."
    )


def nexhealth_api_contract_from_config(config: Any) -> NexHealthAPIContract:
    """Resolve an AuthConfig-like object to a normalized contract target."""
    for attr in ("nexhealth_api_contract", "api_contract"):
        if hasattr(config, attr):
            value = getattr(config, attr)
            if value is not None:
                return normalize_nexhealth_api_contract(value)

    for attr in ("api_version", "nexhealth_api_version"):
        if hasattr(config, attr):
            value = getattr(config, attr)
            if value is not None:
                return normalize_nexhealth_api_contract(value)

    return normalize_nexhealth_api_contract(None)
