"""Rewrite published workflow definitions onto the current trigger vocabulary.

The eleven pre-rearchitecture trigger types were replaced by six.
``upconvert_legacy_trigger`` converts their stored JSON on *load*, so nothing
broke — but it means the database still holds the old shape and the converter
has to stay. This rewrites definitions at rest so it can eventually be deleted.

Safe by construction:

* **Dry run by default.** Nothing is written without ``--commit``.
* **Every rewrite is verified before it is saved.** The converted definition is
  re-validated, and its trigger and node graph are compared against the
  original's. A definition that would behave differently is reported and left
  alone rather than written.
* **Published versions are immutable everywhere else in this system**, and that
  is deliberate — runs pin a version id. This script is the one exception, so it
  rewrites the JSON in place rather than creating a version a running campaign
  is not pinned to.

Usage::

    python -m src.app.scripts.migrate_workflow_triggers            # report only
    python -m src.app.scripts.migrate_workflow_triggers --commit
    python -m src.app.scripts.migrate_workflow_triggers --institution <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select

from src.app.database import get_system_db_session
from src.app.models.automation_workflow import AutomationWorkflowVersion
from src.app.services.automation.definition_schema import (
    LEGACY_TRIGGER_TYPES,
    WorkflowDefinition,
    upconvert_legacy_trigger,
)

logger = logging.getLogger(__name__)


@dataclass
class MigrationReport:
    scanned: int = 0
    already_current: int = 0
    rewritten: int = 0
    skipped: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"scanned          {self.scanned}",
            f"already current  {self.already_current}",
            f"rewritten        {self.rewritten}",
            f"skipped          {len(self.skipped)}",
        ]
        lines += [f"  - {reason}" for reason in self.skipped]
        return "\n".join(lines)


def _legacy_trigger_types(definition: dict) -> list[str]:
    """Retired trigger types present in stored JSON, singular key or plural."""
    triggers = definition.get("triggers")
    if not isinstance(triggers, list):
        single = definition.get("trigger")
        triggers = [single] if isinstance(single, dict) else []
    return [
        t.get("type")
        for t in triggers
        if isinstance(t, dict) and t.get("type") in LEGACY_TRIGGER_TYPES
    ]


def rewrite_definition(definition: dict) -> dict:
    """Return the definition with its triggers written in the current shape."""
    converted = dict(definition)
    triggers = converted.pop("trigger", None)
    if triggers is not None:
        converted["triggers"] = [triggers]
    converted["triggers"] = [
        upconvert_legacy_trigger(t) for t in converted.get("triggers") or []
    ]
    return converted


def _behaviour_matches(before: dict, after: dict) -> str | None:
    """None when the rewrite is equivalent, otherwise why it is not.

    Both sides are loaded through the schema, which applies the same conversion
    on read. If they do not agree, the rewrite changed something and must not be
    written.
    """
    try:
        original = WorkflowDefinition.model_validate(before)
        rewritten = WorkflowDefinition.model_validate(after)
    except Exception as exc:  # noqa: BLE001 — reported, not raised
        return f"does not validate: {exc}"

    if original.model_dump(mode="json") != rewritten.model_dump(mode="json"):
        return "would not load identically"
    return None


async def migrate(*, commit: bool, institution_id: str | None) -> MigrationReport:
    report = MigrationReport()

    async with get_system_db_session(
        "script", external_id="migrate_workflow_triggers"
    ) as session:
        stmt = select(AutomationWorkflowVersion)
        if institution_id:
            stmt = stmt.where(
                AutomationWorkflowVersion.institution_id == institution_id
            )
        versions = (await session.execute(stmt)).scalars().all()

        for version in versions:
            report.scanned += 1
            definition = version.definition
            if not isinstance(definition, dict):
                report.skipped.append(f"{version.id}: definition is not an object")
                continue

            legacy = _legacy_trigger_types(definition)
            if not legacy and "triggers" in definition:
                report.already_current += 1
                continue

            rewritten = rewrite_definition(definition)
            problem = _behaviour_matches(definition, rewritten)
            if problem is not None:
                report.skipped.append(f"{version.id}: {problem}")
                continue

            logger.info(
                "workflow version %s: %s -> %s",
                version.id,
                ", ".join(legacy) or "singular trigger key",
                ", ".join(t.get("type", "?") for t in rewritten["triggers"]),
            )
            if commit:
                version.definition = rewritten
            report.rewritten += 1

        if commit:
            await session.commit()

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write the rewrites. Without this the script only reports.",
    )
    parser.add_argument(
        "--institution",
        default=None,
        help="Limit to one institution id. Useful for a staged rollout.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    report = asyncio.run(
        migrate(commit=args.commit, institution_id=args.institution)
    )
    print(report.render())
    if not args.commit:
        print("\nDry run. Re-run with --commit to write these changes.")


if __name__ == "__main__":
    main()
