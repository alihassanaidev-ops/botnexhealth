"""Authoritative workflow node capabilities and graph-edge semantics.

The registry is independent of Pydantic and the dispatcher so schema validation,
publish validation, dry-run, runtime contract tests, and API clients can share one
support contract without import cycles.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


NODE_REGISTRY_VERSION = "1.0"


@dataclass(frozen=True)
class NodeCapability:
    node_type: str
    outgoing_fields: tuple[str, ...]
    authorable: bool = True
    runtime_supported: bool = True
    dry_run_supported: bool = True
    legacy: bool = False

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["outgoing_fields"] = list(self.outgoing_fields)
        return value


NODE_CAPABILITIES: dict[str, NodeCapability] = {
    "wait": NodeCapability("wait", ("next_node_id",)),
    "wait_for_sms_reply": NodeCapability(
        "wait_for_sms_reply", ("next_node_id",), authorable=False, legacy=True
    ),
    "drip": NodeCapability("drip", ("next_node_id",)),
    "send_sms": NodeCapability("send_sms", ("next_node_id",)),
    "retell_sms_conversation": NodeCapability(
        "retell_sms_conversation", ("next_node_id",)
    ),
    "send_voice": NodeCapability("send_voice", ("next_node_id",)),
    "send_email": NodeCapability("send_email", ("next_node_id",)),
    "update_patient_status": NodeCapability(
        "update_patient_status", ("next_node_id",)
    ),
    "update_appointment": NodeCapability("update_appointment", ("next_node_id",)),
    "update_gotracker_appointment": NodeCapability(
        "update_gotracker_appointment", ("next_node_id",)
    ),
    "json_mapper": NodeCapability("json_mapper", ("next_node_id",)),
    "llm": NodeCapability("llm", ("next_node_id",)),
    "condition": NodeCapability(
        "condition", ("true_next_node_id", "false_next_node_id")
    ),
    "exit": NodeCapability("exit", ()),
}


def node_type(node: object) -> str | None:
    value = node.get("type") if isinstance(node, Mapping) else getattr(node, "type", None)
    return str(value) if value is not None else None


def node_id(node: object) -> str | None:
    value = node.get("id") if isinstance(node, Mapping) else getattr(node, "id", None)
    return str(value) if value is not None else None


def capability_for(node: object) -> NodeCapability | None:
    kind = node_type(node)
    return NODE_CAPABILITIES.get(kind) if kind else None


def outgoing_references(node: object) -> tuple[tuple[str, str], ...]:
    """Return ``(field_name, target_node_id)`` for any registered node."""
    capability = capability_for(node)
    if capability is None:
        return ()
    refs: list[tuple[str, str]] = []
    for field_name in capability.outgoing_fields:
        value = (
            node.get(field_name)
            if isinstance(node, Mapping)
            else getattr(node, field_name, None)
        )
        refs.append((field_name, str(value) if value is not None else ""))
    return tuple(refs)


def public_capabilities() -> list[dict[str, Any]]:
    return [capability.as_dict() for capability in NODE_CAPABILITIES.values()]
