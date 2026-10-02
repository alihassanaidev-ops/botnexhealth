"""Split (A/B) node: schema rules, stable assignment, and graph wiring."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.app.services.automation.definition_schema import (
    SplitNode,
    WorkflowDefinition,
)
from src.app.services.automation.dry_run import simulate_run
from src.app.services.automation.node_registry import (
    NODE_CAPABILITIES,
    outgoing_references,
)
from src.app.services.automation.split_assignment import (
    BUCKET_COUNT,
    assign_branch,
    bucket_for,
)


def _split(**overrides) -> SplitNode:
    payload = {
        "type": "split",
        "id": "ab",
        "subject": "Reminder wording",
        "branches": [
            {"label": "Variant A", "weight": 50, "next_node_id": "n-a"},
            {"label": "Variant B", "weight": 50, "next_node_id": "n-b"},
        ],
    }
    payload.update(overrides)
    return SplitNode.model_validate(payload)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_weights_must_sum_to_one_hundred() -> None:
    """A 30/30 split that silently ran 50/50 would invalidate the experiment."""
    with pytest.raises(ValidationError, match="must sum to 100"):
        _split(
            branches=[
                {"label": "A", "weight": 30, "next_node_id": "n-a"},
                {"label": "B", "weight": 30, "next_node_id": "n-b"},
            ]
        )


def test_duplicate_labels_are_rejected() -> None:
    """The label is the analytics dimension, so two arms sharing one would merge."""
    with pytest.raises(ValidationError, match="duplicate split branch label"):
        _split(
            branches=[
                {"label": "A", "weight": 50, "next_node_id": "n-a"},
                {"label": "a", "weight": 50, "next_node_id": "n-b"},
            ]
        )


def test_a_split_needs_at_least_two_arms() -> None:
    with pytest.raises(ValidationError):
        _split(branches=[{"label": "A", "weight": 100, "next_node_id": "n-a"}])


def test_registry_reports_one_port_per_branch() -> None:
    node = _split()
    assert NODE_CAPABILITIES["split"].has_variable_ports
    assert outgoing_references(node) == (
        ("branches[0].next_node_id", "n-a"),
        ("branches[1].next_node_id", "n-b"),
    )


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


def test_assignment_is_stable_for_a_run() -> None:
    """The property the whole design rests on.

    A retried step or a run resumed from a timer must re-derive the same arm; a
    contact that switched variants mid-run would both confuse the patient and
    corrupt the experiment it was part of.
    """
    node = _split()
    run_id = str(uuid4())
    first, bucket = assign_branch(node, run_id=run_id)
    for _ in range(20):
        again, again_bucket = assign_branch(node, run_id=run_id)
        assert again.label == first.label
        assert again_bucket == bucket


def test_two_splits_in_one_workflow_assign_independently() -> None:
    """Node id is in the digest, so a run is not pinned to "the left arm"."""
    run_id = str(uuid4())
    buckets = {bucket_for(run_id, f"split-{i}") for i in range(50)}
    assert len(buckets) > 1


def test_weights_are_respected_across_a_population() -> None:
    """Run ids are effectively random, so the split converges on its weights."""
    node = _split(
        branches=[
            {"label": "A", "weight": 80, "next_node_id": "n-a"},
            {"label": "B", "weight": 20, "next_node_id": "n-b"},
        ]
    )
    counts = {"A": 0, "B": 0}
    trials = 4000
    for _ in range(trials):
        branch, _ = assign_branch(node, run_id=str(uuid4()))
        counts[branch.label] += 1
    assert 0.75 < counts["A"] / trials < 0.85


def test_bucket_stays_in_range() -> None:
    for i in range(500):
        assert 0 <= bucket_for(str(uuid4()), f"n-{i}") < BUCKET_COUNT


def test_every_bucket_maps_to_an_arm() -> None:
    """Weights are exhaustive, so no bucket can fall through to a dead end."""
    node = _split(
        branches=[
            {"label": "A", "weight": 34, "next_node_id": "n-a"},
            {"label": "B", "weight": 33, "next_node_id": "n-b"},
            {"label": "C", "weight": 33, "next_node_id": "n-c"},
        ]
    )
    seen: set[str] = set()
    for _ in range(3000):
        branch, _ = assign_branch(node, run_id=str(uuid4()))
        seen.add(branch.label)
    assert seen == {"A", "B", "C"}


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


def test_dry_run_walks_a_named_split_branch() -> None:
    definition = WorkflowDefinition.model_validate(
        {
            "schema_version": "1.0",
            "trigger": {"type": "manual"},
            "entry_node_id": "ab",
            "nodes": [
                {
                    "type": "split",
                    "id": "ab",
                    "branches": [
                        {"label": "Variant A", "weight": 50, "next_node_id": "a"},
                        {"label": "Variant B", "weight": 50, "next_node_id": "b"},
                    ],
                },
                {"type": "exit", "id": "a", "outcome": "went_a"},
                {"type": "exit", "id": "b", "outcome": "went_b"},
            ],
        }
    )
    # Unset previews the first arm — the one the author just wrote.
    assert simulate_run(definition).outcome == "went_a"
    assert (
        simulate_run(definition, switch_case_choices={"ab": "Variant B"}).outcome
        == "went_b"
    )
