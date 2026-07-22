"""_validate_invite_role gates the institution /users/invite endpoint.

Regression guard: the UI offers Staff (and the endpoint requires+resolves a
location for STAFF exactly like LOCATION_ADMIN), but the validator used to
reject STAFF with "Invalid role 'STAFF'. Allowed: INSTITUTION_ADMIN,
LOCATION_ADMIN". All three institution roles must be accepted; SUPER_ADMIN and
junk must not.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.app.api.routes.institution_portal import _validate_invite_role


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("INSTITUTION_ADMIN", "INSTITUTION_ADMIN"),
        ("LOCATION_ADMIN", "LOCATION_ADMIN"),
        ("STAFF", "STAFF"),
        ("staff", "STAFF"),  # case-insensitive
        ("  staff  ", "STAFF"),  # trimmed
    ],
)
def test_allows_institution_roles(raw: str, expected: str) -> None:
    assert _validate_invite_role(raw) == expected


@pytest.mark.parametrize("raw", ["SUPER_ADMIN", "GROUP_ADMIN", "", "owner"])
def test_rejects_non_institution_roles(raw: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_invite_role(raw)
    assert exc.value.status_code == 422
