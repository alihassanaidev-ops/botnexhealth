"""Seed demo data for local UI review.

Creates two institutions with locations, one user per role (password
login), contacts, ~3 months of calls with varied outcomes/tags/sentiment,
callbacks, and basic setup data (appointment types, providers, insurance
plans). Finally rebuilds the ``call_metrics_daily`` rollup so dashboard
graphs are populated.

Idempotent: wipes any prior demo rows (matched by institution slug) and
recreates them. Safe to re-run.

    python -m src.app.scripts.seed_demo_data

Login for every seeded account is the same password: ``LocalDev123!``.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.app.config import settings
from src.app.models.call import Call, CallStatus
from src.app.models.contact import Contact
from src.app.models.contact_location_access import ContactLocationAccess
from src.app.models.institution import Institution
from src.app.models.institution_group import InstitutionGroup
from src.app.models.institution_appointment_type import InstitutionAppointmentType
from src.app.models.institution_location import InstitutionLocation
from src.app.models.institution_provider import InstitutionProvider
from src.app.models.insurance_plan import InsurancePlan
from src.app.models.user import InviteStatus, User, UserRole
from src.app.services.dashboard_rollup import recompute_window
from src.app.services.password_service import PasswordService
from src.app.services.workflow_status_service import WorkflowStatusService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_demo_data")

PASSWORD = "LocalDev123!"
DAYS_BACK = 90
SYSTEM_UID = "00000000-0000-0000-0000-000000000000"

random.seed(42)

# institution slug -> definition
INSTITUTIONS = [
    {
        "name": "Bright Smile Dental",
        "slug": "bright-smile-dental",
        "primary": True,  # demo users belong here; gets heavy call volume
        "roi_config": {
            "avg_appointment_value": 285.0,
            "avg_new_patient_value": 950.0,
            "monthly_subscription_cost": 1200.0,
            "staff_hourly_rate": 28.0,
            "avg_call_duration_minutes": 4.5,
        },
        "locations": [
            {"name": "Downtown Clinic", "slug": "downtown", "city": "Austin", "state": "TX", "tz": "America/Chicago", "from": "+15125550101"},
            {"name": "Westside Office", "slug": "westside", "city": "Austin", "state": "TX", "tz": "America/Chicago", "from": "+15125550102"},
        ],
    },
    {
        "name": "Lakeview Orthodontics",
        "slug": "lakeview-orthodontics",
        "primary": False,
        "roi_config": {
            "avg_appointment_value": 420.0,
            "avg_new_patient_value": 1300.0,
            "monthly_subscription_cost": 1500.0,
            "staff_hourly_rate": 30.0,
            "avg_call_duration_minutes": 5.2,
        },
        "locations": [
            {"name": "Main Street", "slug": "main", "city": "Denver", "state": "CO", "tz": "America/Denver", "from": "+17205550201"},
        ],
    },
    {
        # Call-intelligence-only tenant: no PMS, no booking/providers/appointment
        # types. The agent only captures call data; the dashboard + Patients page
        # surface it. Exercises the no-PMS gating end-to-end.
        "name": "Bright Voice Dental",
        "slug": "bright-voice-dental",
        "primary": False,
        "full_accounts": True,  # seed inst.admin + loc.admin + staff so gating is testable
        "pms_type": "none",
        "roi_config": {
            "avg_appointment_value": 0.0,
            "avg_new_patient_value": 0.0,
            "monthly_subscription_cost": 900.0,
            "staff_hourly_rate": 26.0,
            "avg_call_duration_minutes": 3.8,
        },
        "locations": [
            {"name": "Capitol Hill", "slug": "capitol-hill", "city": "Seattle", "state": "WA", "tz": "America/Los_Angeles", "from": "+12065550301", "retell_agent_id": "agent_bright_voice_demo"},
        ],
    },
]

FIRST_NAMES = ["Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia", "Mason",
               "Isabella", "Lucas", "Mia", "Aiden", "Charlotte", "Caleb", "Amelia",
               "Grace", "Henry", "Ella", "Jack", "Lily", "Owen", "Zoe", "Leo", "Nora"]
LAST_NAMES = ["Carter", "Reyes", "Nguyen", "Patel", "Brooks", "Foster", "Hughes",
              "Bennett", "Coleman", "Ward", "Murphy", "Rivera", "Sullivan", "Park",
              "Coleman", "Diaz", "Fleming", "Greer", "Holt", "Iverson"]
SPECIALTIES = ["General Dentistry", "Orthodontics", "Endodontics", "Periodontics", "Hygiene"]
APPT_TYPES = [("New Patient Exam", 60), ("Cleaning", 45), ("Filling", 30),
              ("Crown Prep", 90), ("Emergency Visit", 30), ("Consultation", 30)]
INSURANCE = ["Delta Dental PPO", "Cigna Dental", "Aetna DMO", "MetLife", "Guardian", "United Concordia"]

# status -> (weight, extra tags pool, sentiment bias)
STATUS_WEIGHTS = [
    (CallStatus.APPOINTMENT_BOOKED.value, 28, ["scheduling", "new_patient"], "Positive"),
    (CallStatus.FAQ_HANDLED.value, 18, ["hours", "directions", "pricing"], "Neutral"),
    (CallStatus.INSURANCE_VERIFIED.value, 10, ["insurance"], "Positive"),
    (CallStatus.FINANCIAL_INQUIRY.value, 8, ["billing", "insurance"], "Neutral"),
    (CallStatus.NEEDS_CALLBACK.value, 10, ["follow_up"], "Neutral"),
    (CallStatus.APPOINTMENT_RESCHEDULED.value, 8, ["scheduling"], "Neutral"),
    (CallStatus.APPOINTMENT_CANCELLED.value, 5, ["scheduling"], "Negative"),
    (CallStatus.COMPLAINT.value, 4, ["service", "wait_time"], "Negative"),
    (CallStatus.EMERGENCY.value, 3, ["urgent", "pain"], "Negative"),
    (CallStatus.TRANSFERRED.value, 4, ["transfer"], "Neutral"),
    (CallStatus.NO_ACTION_NEEDED.value, 2, [], "Neutral"),
]
_STATUS_POP = [s for s, w, *_ in STATUS_WEIGHTS for _ in range(w)]
_STATUS_META = {s: (tags, sent) for s, w, tags, sent in STATUS_WEIGHTS}

# No-PMS institutions emit request-style statuses (agent can't transact in a PMS).
NO_PMS_STATUS_WEIGHTS = [
    (CallStatus.NEEDS_BOOKING.value, 30, ["scheduling", "new_patient"], "Positive"),
    (CallStatus.NEEDS_CALLBACK.value, 16, ["follow_up"], "Neutral"),
    (CallStatus.INSURANCE_AND_BILLING.value, 12, ["insurance", "billing"], "Neutral"),
    (CallStatus.FINANCIAL_INQUIRY.value, 8, ["billing"], "Neutral"),
    (CallStatus.NEEDS_RESCHEDULE.value, 8, ["scheduling"], "Neutral"),
    (CallStatus.NEEDS_CANCELLATION.value, 5, ["scheduling"], "Negative"),
    (CallStatus.COMPLAINT.value, 4, ["service", "wait_time"], "Negative"),
    (CallStatus.EMERGENCY.value, 3, ["urgent", "pain"], "Negative"),
    (CallStatus.NO_ACTION_NEEDED.value, 2, [], "Neutral"),
]
_NO_PMS_STATUS_POP = [s for s, w, *_ in NO_PMS_STATUS_WEIGHTS for _ in range(w)]
_NO_PMS_STATUS_META = {s: (tags, sent) for s, w, tags, sent in NO_PMS_STATUS_WEIGHTS}

# DSO/group demo: a group owning all seeded institutions + a GROUP_ADMIN.
GROUP_NAME = "Bright Dental Group"
GROUP_SLUG = "bright-dental-group"


async def _wipe(session: AsyncSession, slugs: list[str]) -> None:
    """Delete prior demo rows (FK-safe order) for the given institution slugs."""
    # Demo group + its GROUP_ADMIN user (the user has no institution_id, so it
    # isn't covered by the institution-scoped deletes below).
    await session.execute(
        text(
            "DELETE FROM users WHERE group_id IN "
            "(SELECT id FROM institution_groups WHERE slug = :slug)"
        ),
        {"slug": GROUP_SLUG},
    )
    await session.execute(
        text("DELETE FROM institution_groups WHERE slug = :slug"), {"slug": GROUP_SLUG}
    )

    rows = (await session.execute(
        select(Institution.id).where(Institution.slug.in_(slugs))
    )).scalars().all()
    if not rows:
        await session.commit()
        return
    ids = list(rows)
    for table in ("calls", "call_metrics_daily", "contacts",
                  "institution_appointment_types", "institution_providers",
                  "insurance_plans", "users", "institution_locations"):
        await session.execute(
            text(f"DELETE FROM {table} WHERE institution_id = ANY(:ids)"), {"ids": ids}
        )
    await session.execute(text("DELETE FROM institutions WHERE id = ANY(:ids)"), {"ids": ids})
    await session.commit()
    logger.info("Wiped %d existing demo institution(s).", len(ids))


def _mk_user(email: str, role: str, institution_id: str | None, location_id: str | None) -> User:
    return User(
        email=email,
        role=role,
        institution_id=institution_id,
        location_id=location_id,
        password_hash=PasswordService.hash_password(PASSWORD),
        password_set_at=datetime.now(timezone.utc),
        is_active=True,
        invite_status=InviteStatus.ACCEPTED.value,
    )


async def main() -> None:
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set.")

    engine = create_async_engine(settings.database_url, echo=False)
    Session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    today = datetime.now(timezone.utc).date()
    created_users: list[tuple[str, str]] = []

    try:
        async with Session() as session:
            # SUPER_ADMIN context so this works even if the role enforces RLS.
            await session.execute(text(
                "SELECT set_config('app.context_type','user',false),"
                "set_config('app.role','SUPER_ADMIN',false),"
                "set_config('app.user_id',:uid,false)"
            ), {"uid": SYSTEM_UID})

            await _wipe(session, [i["slug"] for i in INSTITUTIONS])

            min_date = today - timedelta(days=DAYS_BACK)

            created_institutions: list[Institution] = []
            for inst_def in INSTITUTIONS:
                inst = Institution(
                    name=inst_def["name"], slug=inst_def["slug"], is_active=True,
                    location_limit=len(inst_def["locations"]),
                    roi_config=inst_def["roi_config"],
                    billing_email=f"billing@{inst_def['slug']}.example",
                    pms_type=inst_def.get("pms_type", "nexhealth"),
                )
                session.add(inst)
                await session.flush()
                created_institutions.append(inst)

                # Seed the default workflow statuses for this institution, then
                # keep the list so we can assign some to calls below.
                ws_svc = WorkflowStatusService(session)
                await ws_svc.seed_defaults(inst.id)
                inst_statuses = await ws_svc.list_statuses(inst.id)

                locations: list[InstitutionLocation] = []
                for loc_def in inst_def["locations"]:
                    loc = InstitutionLocation(
                        institution_id=inst.id, name=loc_def["name"], slug=loc_def["slug"],
                        is_active=True, timezone=loc_def["tz"], city=loc_def["city"],
                        state=loc_def["state"], twilio_from_number=loc_def["from"],
                        address=f"{random.randint(100, 999)} Main St",
                        phone=loc_def["from"],
                        retell_agent_id=loc_def.get("retell_agent_id"),
                    )
                    session.add(loc)
                    locations.append(loc)
                await session.flush()

                # Users — one per role on the primary institution; an admin on the other.
                if inst_def["primary"] or inst_def.get("full_accounts"):
                    slug = inst_def["slug"]
                    accounts = [
                        (f"inst.admin@{slug}.dev", UserRole.INSTITUTION_ADMIN.value, None),
                        (f"loc.admin@{slug}.dev", UserRole.LOCATION_ADMIN.value, locations[0].id),
                        (f"staff@{slug}.dev", UserRole.STAFF.value, locations[0].id),
                    ]
                else:
                    accounts = [(f"inst.admin@{inst_def['slug']}.dev", UserRole.INSTITUTION_ADMIN.value, None)]
                for email, role, loc_id in accounts:
                    session.add(_mk_user(email, role, inst.id, loc_id))
                    created_users.append((email, role))

                # Setup data per location — only for PMS-backed tenants. A no-PMS
                # tenant has no providers / appointment types / insurance plans.
                no_pms = inst_def.get("pms_type") == "none"
                for loc in ([] if no_pms else locations):
                    for i, plan in enumerate(random.sample(INSURANCE, 4)):
                        session.add(InsurancePlan(institution_id=inst.id, location_id=loc.id, name=plan))
                    for i, (atype, dur) in enumerate(APPT_TYPES):
                        session.add(InstitutionAppointmentType(
                            institution_id=inst.id, location_id=loc.id, source="manual",
                            source_id=f"appt-{loc.slug}-{i}", name=atype, duration_minutes=dur,
                        ))
                    for i in range(random.randint(3, 5)):
                        fn, ln = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
                        session.add(InstitutionProvider(
                            institution_id=inst.id, location_id=loc.id, source="manual",
                            source_id=f"prov-{loc.slug}-{i}", name=f"Dr. {fn} {ln}",
                            first_name=fn, last_name=ln, specialty=random.choice(SPECIALTIES),
                        ))

                # Contacts
                n_contacts = 60 if inst_def["primary"] else 25
                contacts: list[Contact] = []
                for _ in range(n_contacts):
                    fn, ln = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
                    c = Contact(
                        institution_id=inst.id, first_name=fn, last_name=ln,
                        full_name=f"{fn} {ln}", is_new_patient=random.random() < 0.35,
                    )
                    # Encrypted callback number (the .phone setter encrypts + hashes).
                    # Valid NANP area code + 555-01xx fictional range so it normalizes.
                    c.phone = f"+1{random.choice(('512', '737', '720', '303', '415'))}555{random.randint(100, 199):03d}{random.randint(0, 9)}"
                    session.add(c)
                    contacts.append(c)
                await session.flush()

                # No-PMS demo: a parent/child pair sharing one phone (auto-match
                # keeps them separate by name), plus one pre-merged alias so the
                # Patients page shows the "linked" badge and the merge/unmerge UI.
                if no_pms and len(contacts) >= 2:
                    shared_phone = "+12065550777"
                    parent = Contact(institution_id=inst.id, first_name="Maria",
                                     last_name="Gomez", full_name="Maria Gomez")
                    parent.phone = shared_phone
                    child = Contact(institution_id=inst.id, first_name="Diego",
                                    last_name="Gomez", full_name="Diego Gomez")
                    child.phone = shared_phone
                    session.add_all([parent, child])
                    # Link the 2nd seeded contact as an alias of the 1st.
                    contacts[1].merged_into_id = contacts[0].id
                    await session.flush()
                    contacts.extend([parent, child])

                # Calls across the window. Track (contact, location) pairs so we
                # can mirror the webhook's ContactLocationAccess grants below —
                # without them, location-scoped users can't see any patients.
                access_pairs: set[tuple[str, str]] = set()
                per_day_range = (4, 11) if inst_def["primary"] else (1, 4)
                for day_offset in range(DAYS_BACK + 1):
                    call_day = min_date + timedelta(days=day_offset)
                    weekday = call_day.weekday()
                    if weekday == 6:  # quieter Sundays
                        day_calls = random.randint(0, 2)
                    else:
                        day_calls = random.randint(*per_day_range)
                        if weekday == 5:
                            day_calls = max(1, day_calls // 2)
                    for _ in range(day_calls):
                        loc = random.choice(locations)
                        contact = random.choice(contacts)
                        status_pop = _NO_PMS_STATUS_POP if no_pms else _STATUS_POP
                        status_meta = _NO_PMS_STATUS_META if no_pms else _STATUS_META
                        status = random.choice(status_pop)
                        extra_tags, sent_bias = status_meta[status]
                        tags = [status] + random.sample(extra_tags, min(len(extra_tags), random.randint(0, 2)))
                        sentiment = sent_bias if random.random() < 0.6 else random.choice(["Positive", "Neutral", "Negative"])
                        hh = random.randint(8, 17)
                        mm = random.choice([0, 7, 15, 23, 30, 42, 51])
                        call_t = time(hh, mm, tzinfo=timezone.utc)
                        created = datetime.combine(call_day, call_t)
                        booking_statuses = (CallStatus.APPOINTMENT_BOOKED.value, CallStatus.NEEDS_BOOKING.value)
                        is_new = contact.is_new_patient and status in booking_statuses and random.random() < 0.6
                        needs_cb = status == CallStatus.NEEDS_CALLBACK.value
                        call = Call(
                            institution_id=inst.id, location_id=loc.id, contact_id=contact.id,
                            call_direction="inbound",
                            call_status=status,
                            call_tags=",".join(tags),
                            patient_sentiment=sentiment,
                            call_date=call_day, call_time=call_t,
                            call_duration_seconds=random.randint(45, 540),
                            is_new_patient=is_new,
                            is_complaint=status == CallStatus.COMPLAINT.value,
                            is_insurance_billing=status in (
                                CallStatus.INSURANCE_AND_BILLING.value,
                                CallStatus.INSURANCE_VERIFIED.value,
                                CallStatus.INSURANCE_UNVERIFIED.value,
                                CallStatus.FINANCIAL_INQUIRY.value,
                            ),
                            times_called=random.randint(1, 3) if needs_cb else 1,
                            created_at=created, updated_at=created,
                        )
                        if needs_cb:
                            call.preferred_callback_datetime = created + timedelta(hours=random.randint(2, 48))
                            if call_day < today - timedelta(days=3) and random.random() < 0.6:
                                call.callback_resolved = True
                                call.callback_resolved_at = created + timedelta(hours=random.randint(3, 60))
                        # Demo: a staffer has triaged ~45% of calls with a workflow status.
                        if inst_statuses and random.random() < 0.45:
                            call.workflow_status_id = random.choice(inst_statuses).id
                        session.add(call)
                        access_pairs.add((contact.id, loc.id))

                # Contact↔location visibility grants (RLS) — mirrors what the
                # webhook does per call, so LOCATION_ADMIN/STAFF see their
                # patients. For a no-PMS (single-location) tenant, also grant any
                # contact that happened to get no calls (e.g. the parent/child
                # demo pair) so the whole directory is visible.
                if no_pms:
                    for c in contacts:
                        access_pairs.add((c.id, locations[0].id))
                for contact_id, location_id in access_pairs:
                    session.add(ContactLocationAccess(
                        institution_id=inst.id, contact_id=contact_id, location_id=location_id,
                    ))

                await session.flush()
                logger.info("Seeded institution %s (%d locations).", inst_def["name"], len(locations))

            # DSO/group demo: one group owning every seeded institution, plus a
            # read-only GROUP_ADMIN to exercise cross-practice oversight.
            group = InstitutionGroup(name=GROUP_NAME, slug=GROUP_SLUG)
            session.add(group)
            await session.flush()
            for inst in created_institutions:
                inst.group_id = group.id
            group_admin_email = f"group.admin@{GROUP_SLUG}.dev"
            session.add(User(
                email=group_admin_email,
                role=UserRole.GROUP_ADMIN.value,
                institution_id=None,
                location_id=None,
                group_id=group.id,
                password_hash=PasswordService.hash_password(PASSWORD),
                password_set_at=datetime.now(timezone.utc),
                is_active=True,
                invite_status=InviteStatus.ACCEPTED.value,
            ))
            created_users.append((group_admin_email, UserRole.GROUP_ADMIN.value))
            logger.info("Seeded group %s with %d institutions.", GROUP_NAME, len(created_institutions))

            await session.commit()

            # Rebuild rollup so dashboard graphs are populated.
            logger.info("Rebuilding call_metrics_daily rollup %s..%s", min_date, today)
            await recompute_window(session, start_date=min_date, end_date=today)
            await session.commit()
    finally:
        await engine.dispose()

    print("\n=== Demo data seeded. Accounts (password: %s) ===" % PASSWORD)
    print(f"  {'admin@local.dev':32} SUPER_ADMIN (existing)")
    for email, role in created_users:
        print(f"  {email:32} {role}")


if __name__ == "__main__":
    asyncio.run(main())
