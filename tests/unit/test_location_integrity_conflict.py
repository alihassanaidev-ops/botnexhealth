"""The location save handlers must name the constraint that actually failed.

A handler that assumes the slug lost sent operators renaming a slug that was
never the problem, while the real rejection was the NexHealth mapping index.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from src.app.api.routes.admin_institutions import _location_integrity_conflict
from src.app.database import integrity_error_constraint


class _AsyncpgError(Exception):
    """Stands in for an asyncpg violation, which carries constraint_name."""

    def __init__(self, message: str, constraint_name: str | None = None) -> None:
        super().__init__(message)
        self.constraint_name = constraint_name


class _Diag:
    def __init__(self, constraint_name: str | None) -> None:
        self.constraint_name = constraint_name


class _PsycopgError(Exception):
    """Stands in for a psycopg violation, which carries diag.constraint_name."""

    def __init__(self, message: str, constraint_name: str | None = None) -> None:
        super().__init__(message)
        self.diag = _Diag(constraint_name)


def _integrity_error(orig: Exception) -> IntegrityError:
    return IntegrityError("INSERT INTO institution_locations ...", {}, orig)


def _conflict(orig: Exception) -> str:
    exc = _integrity_error(orig)
    return _location_integrity_conflict(
        exc,
        slug="relaxation-dentall",
        subdomain="silora-demo-practice",
        nexhealth_location_id="358579",
    ).detail


def test_constraint_name_read_from_asyncpg_error() -> None:
    exc = _integrity_error(_AsyncpgError("duplicate key", "uq_some_constraint"))
    assert integrity_error_constraint(exc) == "uq_some_constraint"


def test_constraint_name_read_from_psycopg_diag() -> None:
    exc = _integrity_error(_PsycopgError("duplicate key", "uq_some_constraint"))
    assert integrity_error_constraint(exc) == "uq_some_constraint"


def test_constraint_name_read_through_chained_cause() -> None:
    """SQLAlchemy's asyncpg dialect wraps the driver error one level deeper."""
    wrapper = Exception("wrapped")
    wrapper.__cause__ = _AsyncpgError("duplicate key", "uq_nested_constraint")
    exc = _integrity_error(wrapper)
    assert integrity_error_constraint(exc) == "uq_nested_constraint"


def test_unknown_constraint_returns_none_rather_than_guessing() -> None:
    exc = _integrity_error(_AsyncpgError("something failed"))
    assert integrity_error_constraint(exc) is None


def test_self_referential_cause_does_not_loop() -> None:
    orig = _AsyncpgError("duplicate key")
    orig.__cause__ = orig
    assert integrity_error_constraint(_integrity_error(orig)) is None


def test_nexhealth_mapping_violation_is_not_reported_as_a_slug_conflict() -> None:
    detail = _conflict(
        _AsyncpgError(
            "duplicate key value violates unique constraint",
            "uq_institution_locations_nexhealth_mapping",
        )
    )
    assert "358579" in detail
    assert "silora-demo-practice" in detail
    assert "slug" not in detail.lower()
    assert "race condition" not in detail.lower()


def test_slug_violation_is_reported_as_a_slug_conflict() -> None:
    detail = _conflict(
        _AsyncpgError(
            "duplicate key value violates unique constraint",
            "uq_institution_locations_inst_slug",
        )
    )
    assert "relaxation-dentall" in detail
    assert "this institution" in detail


def test_subdomain_guard_trigger_is_attributed_to_the_subdomain() -> None:
    """The trigger raises unique_violation without naming a constraint."""
    detail = _conflict(
        _AsyncpgError(
            "NexHealth subdomain silora-demo-practice is already bound to "
            "another institution"
        )
    )
    assert "silora-demo-practice" in detail
    assert "different institution" in detail


def test_unrecognized_violation_does_not_blame_a_specific_field() -> None:
    detail = _conflict(_AsyncpgError("some new constraint we have not seen"))
    assert "race condition" not in detail.lower()
    assert "slug" in detail.lower() and "NexHealth" in detail


@pytest.mark.parametrize(
    "constraint",
    [
        "uq_institution_locations_nexhealth_mapping",
        "uq_institution_locations_inst_slug",
        None,
    ],
)
def test_every_violation_is_a_409(constraint: str | None) -> None:
    exc = _integrity_error(_AsyncpgError("duplicate key", constraint))
    conflict = _location_integrity_conflict(
        exc, slug="s", subdomain=None, nexhealth_location_id=None
    )
    assert conflict.status_code == 409
