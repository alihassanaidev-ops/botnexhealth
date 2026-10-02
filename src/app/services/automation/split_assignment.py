"""Deterministic branch assignment for workflow Split nodes.

The engine retries steps and resumes runs from timers, so a split arm cannot be
drawn from a random source: the same run would re-roll on every resume and drift
between variants, which both breaks the contact's experience and makes the
experiment's own numbers meaningless.

Instead the arm is *derived* from ``(run_id, node_id)``. The same run always
lands on the same arm of the same node, no state is stored to make that true,
and two different splits in one workflow assign independently because the node
id is part of the digest. Across a population of runs the ids are effectively
random, so the observed distribution converges on the authored weights.
"""

from __future__ import annotations

import hashlib

from src.app.services.automation.definition_schema import SplitBranch, SplitNode

#: Weights are whole percents, so a hundred buckets is exactly enough.
BUCKET_COUNT = 100


def bucket_for(run_id: str, node_id: str) -> int:
    """Return the stable bucket in ``[0, 100)`` for one run at one split node."""
    digest = hashlib.sha256(f"{run_id}:{node_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % BUCKET_COUNT


def assign_branch(node: SplitNode, *, run_id: str) -> tuple[SplitBranch, int]:
    """Pick the arm this run belongs to, with the bucket that chose it.

    The bucket is returned alongside the branch so a trace can show *why* a
    contact went left rather than right — without it, a support question about
    one patient's variant has no answer beyond "the hash said so".
    """
    bucket = bucket_for(run_id, node.id)
    cumulative = 0
    for branch in node.branches:
        cumulative += branch.weight
        if bucket < cumulative:
            return branch, bucket
    # Unreachable while the schema enforces weights summing to 100; kept so a
    # definition written before that rule could not strand a run mid-graph.
    return node.branches[-1], bucket
