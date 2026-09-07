"""Who reads patient contact details inline, and who has to ask for them.

This is the single answer to "should this response carry the real phone number,
or the last four digits and a reveal button". It exists because that question
used to be answered independently in six places — ``calls.py``, ``contacts.py``,
``callbacks.py``, ``dashboard.py``, ``enquiries.py`` and
``universal/patients.py`` — and the answers had drifted into contradicting each
other. The patient directory treated INSTITUTION_ADMIN as the *least* trusted
clinic role, masking contact details it served inline to STAFF; the call
surfaces treated the same role as more trusted than STAFF. Same user, same
patient, opposite rules depending on which page they opened.

The rule now:

* **Clinic administrators read inline.** INSTITUTION_ADMIN and LOCATION_ADMIN
  run the practice whose patients these are. They are in the circle of care and
  they clicked "Reveal" every single time it was offered, which makes it a
  speed bump rather than a control. They keep an audit row per view — see the
  ``inline_phi`` flag written by the call routes — so access stays attributable;
  what is gone is the pretence that the extra click was a decision.
* **STAFF keeps the reveal step** on call surfaces. Front-desk screens sit in
  waiting rooms, which is the one place the reveal-on-click pattern earns its
  keep.
* **SUPER_ADMIN never reads inline.** Platform-level, outside the circle of
  care, and reveal itself is gated behind break-glass. That exclusion is the
  thing that makes serving everyone else inline defensible, so it stays.
* **GROUP_ADMIN never reaches these routes** (rollup endpoints only) and is
  excluded here for completeness rather than because it could get this far.

This governs *presentation of data we hold in full*. It has nothing to do with
the masking applied at write time — ``sms_history_log.to_number_masked`` and the
automation trace metadata store an already-masked string in the column, and no
role check can un-mask those.
"""

from __future__ import annotations

from src.app.models.user import User, UserRole

# The roles that operate the practice day to day and own its patient record.
_INLINE_PHI_ROLES = frozenset(
    {
        UserRole.INSTITUTION_ADMIN.value,
        UserRole.LOCATION_ADMIN.value,
    }
)


def serves_phi_inline(user: User) -> bool:
    """Whether ``user`` receives real contact details instead of masked ones.

    Tenant scoping is enforced separately by each route's dependency; the
    ``institution_id`` check here is belt-only in the sense that a user with no
    institution has no patients to be in the circle of care *for*, so inline
    service would be meaningless rather than merely unauthorised.
    """
    return bool(user.institution_id) and user.role in _INLINE_PHI_ROLES
