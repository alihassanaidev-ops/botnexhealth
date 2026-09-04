"""The builder's TypeScript model must not drift from the definition schema.

Three drifts had accumulated silently and each one cost real capability:

* the ``email_reply`` trigger and the ``email_reply`` wait config existed in the
  backend schema and were absent from ``types/workflow.ts``, so a fully wired
  backend feature was unreachable from the builder;
* ``patient_voice_cooldown_behavior`` / ``patient_voice_cooldown_deadline_field``
  were likewise missing, and the live post-op campaign depends on both — the
  builder could not show or edit fields production was already running on.

This test reads the TypeScript source directly rather than importing it, which
keeps it dependency-free and fast. It is deliberately a *membership* check on
type literals, not a full parse: it catches "a new variant was added on one side
only", which is the failure mode that actually happens.
"""

from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest

from src.app.services.automation.definition_schema import (
    SendVoiceNode,
    WaitForConfig,
    WorkflowNode,
    WorkflowTrigger,
)
from src.app.services.automation.node_registry import NODE_CAPABILITIES

_WEB = Path(__file__).resolve().parents[2] / "nexus-dashboard-web" / "src"
_TYPES = _WEB / "types" / "workflow.ts"
_CATALOG = _WEB / "lib" / "workflow" / "catalog.ts"


def _union_type_literals(annotated: object) -> set[str]:
    """The ``type`` discriminator literals of an Annotated[Union[...]] alias."""
    union = typing.get_args(annotated)[0]
    return {
        typing.get_args(model.model_fields["type"].annotation)[0]
        for model in typing.get_args(union)
    }


def _ts_source(path: Path) -> str:
    if not path.exists():  # pragma: no cover - only when the web app is absent
        pytest.skip(f"{path} not present in this checkout")
    return path.read_text(encoding="utf-8")


def _ts_string_union(source: str, alias: str) -> set[str]:
    """Members of ``export type <alias> = "a" | "b" | ...``."""
    match = re.search(
        rf"export type {alias}\s*=\s*((?:\s*\|?\s*\"[a-z_]+\")+)", source
    )
    assert match, f"could not find `export type {alias}` in the TypeScript model"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def _ts_declared_types(source: str) -> set[str]:
    """Every ``type: "literal"`` discriminator declared in interfaces."""
    return set(re.findall(r'^\s*type:\s*"([a-z_]+)"', source, flags=re.MULTILINE))


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


def test_trigger_union_matches() -> None:
    backend = _union_type_literals(WorkflowTrigger)
    frontend = _ts_string_union(_ts_source(_TYPES), "TriggerType")
    assert backend == frontend, (
        f"trigger types missing from the builder: {sorted(backend - frontend)}; "
        f"unknown to the backend: {sorted(frontend - backend)}"
    )


def test_every_trigger_has_builder_metadata() -> None:
    """A trigger without TRIGGER_META crashes the palette on an icon lookup."""
    backend = _union_type_literals(WorkflowTrigger)
    catalog = _ts_source(_CATALOG)
    block = catalog[catalog.index("TRIGGER_META") :]
    declared = set(re.findall(r"^\s{4}([a-z_]+):\s*\{", block, flags=re.MULTILINE))
    assert backend <= declared, (
        f"TRIGGER_META is missing {sorted(backend - declared)}"
    )


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def test_node_union_matches() -> None:
    backend = _union_type_literals(WorkflowNode)
    frontend = _ts_string_union(_ts_source(_TYPES), "NodeType")
    # `wait_for_sms_reply` is a load-only legacy shape: the registry marks it
    # non-authorable, so the builder is right not to model it.
    legacy = {
        node_type
        for node_type, capability in NODE_CAPABILITIES.items()
        if not capability.authorable
    }
    assert backend - legacy == frontend, (
        f"node types missing from the builder: {sorted(backend - legacy - frontend)}; "
        f"unknown to the backend: {sorted(frontend - backend)}"
    )


def test_every_authorable_node_has_builder_metadata() -> None:
    catalog = _ts_source(_CATALOG)
    block = catalog[catalog.index("NODE_META") :]
    declared = set(re.findall(r"^\s{4}([a-z_]+):\s*\{", block, flags=re.MULTILINE))
    authorable = {
        node_type
        for node_type, capability in NODE_CAPABILITIES.items()
        if capability.authorable
    }
    assert authorable <= declared, f"NODE_META is missing {sorted(authorable - declared)}"


# ---------------------------------------------------------------------------
# Nested unions and individual fields that drifted
# ---------------------------------------------------------------------------


def test_wait_for_union_matches() -> None:
    backend = _union_type_literals(WaitForConfig)
    frontend = _ts_declared_types(_ts_source(_TYPES))
    assert backend <= frontend, (
        f"wait modes missing from the builder: {sorted(backend - frontend)}"
    )


@pytest.mark.parametrize(
    "field_name",
    [
        # Used by the live post-op campaign; the builder could not edit either.
        "patient_voice_cooldown_behavior",
        "patient_voice_cooldown_deadline_field",
        "wait_for_outcome",
        "voice_profile_id",
    ],
)
def test_voice_node_fields_are_modelled(field_name: str) -> None:
    assert field_name in SendVoiceNode.model_fields
    assert field_name in _ts_source(_TYPES), (
        f"SendVoiceNode.{field_name} is not in the builder's TypeScript model"
    )


# ---------------------------------------------------------------------------
# PMS scope (which PMS owns which trigger/node)
# ---------------------------------------------------------------------------


def _ts_pms_map(source: str, alias: str) -> dict[str, set[str]]:
    """Entries of ``export const <alias> = { name: ["pms", ...], ... }``."""
    match = re.search(rf"export const {alias}[^=]*=\s*\{{(.*?)\}}", source, re.DOTALL)
    assert match, f"could not find `export const {alias}` in catalog.ts"
    return {
        name: set(re.findall(r'"([a-z_]+)"', owners))
        for name, owners in re.findall(r"(\w+):\s*\[([^\]]*)\]", match.group(1))
    }


def test_pms_scope_covers_every_trigger_and_node() -> None:
    """A new trigger/node added without a PMS classification is a leak waiting
    to happen — it would be offered to every institution by default."""
    from src.app.services.automation import pms_scope

    triggers = _union_type_literals(WorkflowTrigger)
    nodes = _union_type_literals(WorkflowNode)
    assert triggers == set(pms_scope.TRIGGER_PMS), (
        f"pms_scope.TRIGGER_PMS is missing {sorted(triggers - set(pms_scope.TRIGGER_PMS))}; "
        f"stale entries: {sorted(set(pms_scope.TRIGGER_PMS) - triggers)}"
    )
    assert nodes == set(pms_scope.NODE_PMS), (
        f"pms_scope.NODE_PMS is missing {sorted(nodes - set(pms_scope.NODE_PMS))}; "
        f"stale entries: {sorted(set(pms_scope.NODE_PMS) - nodes)}"
    )


def test_pms_scope_matches_frontend_catalog() -> None:
    """The builder's TRIGGER_PMS/NODE_PMS must mirror the backend ownership map.

    The frontend maps only list *restricted* entries (absent = shared), so
    compare against the backend entries that are not ALL_PMS_TYPES.
    """
    from src.app.services.automation import pms_scope

    catalog = _ts_source(_CATALOG)
    fe_triggers = _ts_pms_map(catalog, "TRIGGER_PMS")
    fe_nodes = _ts_pms_map(catalog, "NODE_PMS")
    be_triggers = {
        name: set(owners)
        for name, owners in pms_scope.TRIGGER_PMS.items()
        if owners != pms_scope.ALL_PMS_TYPES
    }
    be_nodes = {
        name: set(owners)
        for name, owners in pms_scope.NODE_PMS.items()
        if owners != pms_scope.ALL_PMS_TYPES
    }
    assert fe_triggers == be_triggers
    assert fe_nodes == be_nodes
