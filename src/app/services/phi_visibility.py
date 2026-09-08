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

* **Every clinic role reads inline.** INSTITUTION_ADMIN, LOCATION_ADMIN and
  STAFF all work the practice whose patients these are. They are in the circle
  of care and they clicked "Reveal" every single time it was offered, which
  makes it a speed bump rather than a control. They keep an audit row per view —
  see the ``inline_phi`` flag written by the call routes — so access stays
  attributable; what is gone is the pretence that the extra click was a
  decision.

  STAFF was the last holdout, on the argument that front-desk screens sit in
  waiting rooms. It was dropped because the mask it bought was already gone:
  STAFF has always read whole phone numbers and email addresses off the patient
  directory, so anyone minded to read a number simply opened the other screen
  and searched the name. A control one navigation step defeats is not
  protecting against shoulder-surfing; it is only slowing down the front desk
  ringing patients back. Shoulder-surfing is a workstation problem — screen
  lock, monitor placement, session timeout — and those are where it should be
  answered.
* **SUPER_ADMIN never reads inline.** Platform-level, outside the circle of
  care, and reveal itself is gated behind break-glass. That exclusion is the
  thing that makes serving every clinic role inline defensible, so it stays.
* **GROUP_ADMIN never reaches these routes** (rollup endpoints only) and is
  excluded here for completeness rather than because it could get this far.

One consequence worth naming: with every clinic role inline and SUPER_ADMIN
behind break-glass, the phone/transcript/recording reveal endpoints no longer
have a routine caller. They are left in place — they are the audited path, and
break-glass still needs them — but they are now a fallback rather than part of
anybody's daily flow.

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
        UserRole.STAFF.value,
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
