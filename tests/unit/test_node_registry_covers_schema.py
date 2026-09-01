"""Every authorable node type must be registered as a capability.

The registry is what the dispatcher consults before executing a step, and what
the builder's palette filters against. A node type present in the schema but
missing here is invisible in the palette *and* fails its run at dispatch with
"not supported by this engine" — so it looks authored and simply never works.

That is exactly what happened when booking_link and patient_registration were
added: schema, dispatcher branch, API enforcement and tests all existed, and the
nodes were still unusable because nothing registered them. Unit tests that
exercise the schema directly cannot see it. This closes that gap by deriving the
expectation from the schema union rather than a hand-kept list.
"""

from __future__ import annotations

import typing

from src.app.services.automation.definition_schema import WorkflowNode
from src.app.services.automation.node_registry import NODE_CAPABILITIES


def _schema_node_types() -> set[str]:
    """The `type` literal of every member of the WorkflowNode union."""
    # WorkflowNode is Annotated[Union[...], Field(discriminator="type")]
    union = typing.get_args(WorkflowNode)[0]
    types: set[str] = set()
    for member in typing.get_args(union):
        annotation = member.model_fields["type"].annotation
        literal_args = typing.get_args(annotation)
        types.add(literal_args[0] if literal_args else annotation)
    return types


def test_every_schema_node_type_is_registered() -> None:
    missing = _schema_node_types() - set(NODE_CAPABILITIES)
    assert not missing, (
        f"node type(s) {sorted(missing)} exist in the schema but are not in "
        "NODE_CAPABILITIES. The dispatcher would fail any run reaching one, and "
        "the builder palette would not offer it. Add a NodeCapability."
    )


def test_the_registry_does_not_invent_types_the_schema_lacks() -> None:
    """The other direction: a capability with no schema member is dead config.

    wait_for_sms_reply is the known legacy exception — it is kept for published
    definitions and is explicitly not authorable.
    """
    extra = set(NODE_CAPABILITIES) - _schema_node_types()
    assert extra <= {"wait_for_sms_reply"}, (
        f"registry lists {sorted(extra - {'wait_for_sms_reply'})} with no schema node"
    )


def test_the_two_link_nodes_are_runtime_supported() -> None:
    """Guards the specific bug: registered, but flagged unsupported, is the
    same failure with an extra step."""
    for node_type in ("booking_link", "patient_registration"):
        capability = NODE_CAPABILITIES[node_type]
        assert capability.runtime_supported, f"{node_type} would fail at dispatch"
        assert capability.authorable, f"{node_type} would not appear in the palette"
        assert capability.outgoing_fields == ("next_node_id",)
