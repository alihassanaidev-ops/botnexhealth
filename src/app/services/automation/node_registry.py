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
    #: Fixed, singular forward pointers, e.g. ``("next_node_id",)``.
    outgoing_fields: tuple[str, ...]
    #: Variable-count ports, as ``(list_field, item_field)`` pairs. A ``switch``
    #: declares ``(("cases", "next_node_id"),)``: one port per authored case,
    #: alongside its fixed ``default_next_node_id``.
    outgoing_list_fields: tuple[tuple[str, str], ...] = ()
    authorable: bool = True
    runtime_supported: bool = True
    dry_run_supported: bool = True
    legacy: bool = False

    @property
    def has_variable_ports(self) -> bool:
        return bool(self.outgoing_list_fields)

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["outgoing_fields"] = list(self.outgoing_fields)
        value["outgoing_list_fields"] = [
            list(pair) for pair in self.outgoing_list_fields
        ]
        value["has_variable_ports"] = self.has_variable_ports
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
    "booking_link": NodeCapability("booking_link", ("next_node_id",)),
    "patient_registration": NodeCapability(
        "patient_registration", ("next_node_id",)
    ),
    "json_mapper": NodeCapability("json_mapper", ("next_node_id",)),
    "llm": NodeCapability("llm", ("next_node_id",)),
    "condition": NodeCapability(
        "condition", ("true_next_node_id", "false_next_node_id")
    ),
    "switch": NodeCapability(
        "switch",
        ("default_next_node_id",),
        outgoing_list_fields=(("cases", "next_node_id"),),
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


def _read(node: object, field_name: str) -> Any:
    return (
        node.get(field_name)
        if isinstance(node, Mapping)
        else getattr(node, field_name, None)
    )


def outgoing_references(node: object) -> tuple[tuple[str, str], ...]:
    """Return ``(field_path, target_node_id)`` for any registered node.

    Variable ports are reported with an indexed path — ``cases[2].next_node_id``
    — so a validation error points at the case the author actually mis-wired
    rather than at the node as a whole.
    """
    capability = capability_for(node)
    if capability is None:
        return ()
    refs: list[tuple[str, str]] = []
    for field_name in capability.outgoing_fields:
        value = _read(node, field_name)
        refs.append((field_name, str(value) if value is not None else ""))
    for list_field, item_field in capability.outgoing_list_fields:
        items = _read(node, list_field) or []
        for index, item in enumerate(items):
            value = _read(item, item_field)
            refs.append(
                (
                    f"{list_field}[{index}].{item_field}",
                    str(value) if value is not None else "",
                )
            )
    return tuple(refs)


def public_capabilities() -> list[dict[str, Any]]:
    return [capability.as_dict() for capability in NODE_CAPABILITIES.values()]
