"""Named permissions for high-consequence actions (Item 33).

Access control already answers "which clinic and locations may you touch?", and
answers it correctly. What it has never answered is "within your own clinic, are
you senior enough to do *this*?" — so every role that clears the tenant check can
do anything the tenant can do.

Four actions deserve more than that. Two of them write into a live practice's
schedule, which is the most consequential thing the product can do to anyone.

Why permissions rather than more role checks
--------------------------------------------

A role check answers the question at one call site and nowhere else, which is how
two of these four ended up with no check at all — nothing declared that they
needed one, so nothing noticed. A named permission is a fact about the *action*,
declared once, enforced by a dependency, and checkable by a test that fails when
a new high-consequence endpoint appears without one.

No new roles. The five that exist are unchanged; this only records which of them
carry which key.


Scope note — Cloud Service parity
---------------------------------

``WRITE_REPLAY`` and ``WRITE_RESOLVE_CONFLICT`` are the dangerous pair, and the
buttons that perform them for a GoTracker practice live in the **Cloud Service /
Ops UI**, not this repo. They shipped there with Item 2.

They are still defined here, deliberately. Item 33's requirement is that the
distinction exists and is enforced wherever the action is reachable, and this
repo now exposes platform and tenant undeliverable replay paths. Naming both
also gives the Cloud Service a vocabulary to mirror rather than invent, which is
what went wrong the first time: the same idea named differently on each side of
the boundary is the same as not having it.

**Defining a permission here does not gate the Cloud Service's buttons.** Until
that side enforces its half, the protection Item 33 describes is not complete.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, status

from src.app.api.deps import get_current_active_user
from src.app.models.user import User, UserRole

logger = logging.getLogger(__name__)

__all__ = [
    "Permission",
    "ROLE_PERMISSIONS",
    "has_permission",
    "permissions_for",
    "require_permission",
]


class Permission(str, Enum):
    """The four actions Item 33 names.

    Values appear in refusal logs and in the coverage test, so they are part of
    the contract rather than free-form labels.
    """

    #: Read a clinic's synchronisation state. Operational detail about the
    #: practice's connection, not something a front desk needs.
    SYNC_READ = "sync:read"
    #: Re-run a write that failed. Reaches a live practice's records.
    WRITE_REPLAY = "write:replay"
    #: Force through a write that was refused as conflicting. Reaches a live
    #: practice's schedule, and overrides a protection deliberately put there.
    WRITE_RESOLVE_CONFLICT = "write:resolve_conflict"
    #: Change what a campaign says and does — that is, what real patients are
    #: told, and when.
    CAMPAIGN_CONFIGURE = "campaign:configure"


#: Which roles hold which keys.
#:
#: The rule the scope note insists on: **replay and conflict resolution sit above
#: ordinary campaign editing.** Someone trusted to fix the wording of a reminder
#: is not thereby trusted to force an appointment into a dentist's diary, and a
#: location admin runs one site rather than the practice's integration.
#:
#: STAFF hold none of these. That is a change for sync status, which they could
#: read until now.
ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    UserRole.SUPER_ADMIN.value: frozenset(Permission),
    UserRole.INSTITUTION_ADMIN.value: frozenset(
        {
            Permission.SYNC_READ,
            Permission.WRITE_REPLAY,
            Permission.WRITE_RESOLVE_CONFLICT,
            Permission.CAMPAIGN_CONFIGURE,
        }
    ),
    UserRole.LOCATION_ADMIN.value: frozenset(
        {
            Permission.SYNC_READ,
            Permission.CAMPAIGN_CONFIGURE,
        }
    ),
    UserRole.STAFF.value: frozenset(),
    # Read-only oversight across a group's practices, and explicitly never on
    # PHI or setup routes. Nothing here is a read it should gain.
    UserRole.GROUP_ADMIN.value: frozenset(),
}


def permissions_for(role: str | None) -> frozenset[Permission]:
    """Every permission a role carries. Unknown roles carry none."""
    return ROLE_PERMISSIONS.get(role or "", frozenset())


def has_permission(user: User | None, permission: Permission) -> bool:
    """Whether *user* may perform the action *permission* names.

    Fails closed: no user, no role, or a role nobody has mapped yields False. A
    role added to ``UserRole`` without a line in ``ROLE_PERMISSIONS`` gets
    nothing rather than everything.
    """
    if user is None:
        return False
    return permission in permissions_for(getattr(user, "role", None))


def require_permission(permission: Permission) -> Callable:
    """A dependency that refuses anyone without *permission*.

    Layered on top of the existing role dependency rather than replacing it: the
    role check decides which tenant you belong to, this decides whether you are
    senior enough for this particular action. Both must pass.
    """

    async def _dependency(
        current_user: Annotated[User, Depends(get_current_active_user)],
    ) -> User:
        if not has_permission(current_user, permission):
            # The permission, not the role, so an operator reading the log knows
            # which key to grant rather than guessing at the hierarchy.
            logger.warning(
                "permission refused: permission=%s role=%s user=%s",
                permission.value,
                getattr(current_user, "role", None),
                getattr(current_user, "id", None),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires the '{permission.value}' permission",
            )
        return current_user

    # A stable, greppable name. The route-matrix test reads dependency names to
    # classify a route's boundary, and an anonymous closure would appear there
    # as nothing at all.
    _dependency.__name__ = f"require_{permission.name.lower()}"
    return _dependency
