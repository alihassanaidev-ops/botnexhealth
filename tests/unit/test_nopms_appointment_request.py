"""No-PMS 'Appointment Request' notifications: extraction, PHI-free rendering,
template registration, and SMS default.

These institutions can't truly book, so a booking call is a *request* staff
enter manually. The staff email/SMS must carry only non-identifying triage
info — never patient name or DOB.
"""

from __future__ import annotations

import pytest

from src.app.models.email_template import EmailTemplateType
from src.app.models.sms_template import SmsTemplateType
from src.app.services.email_notification_service import _build_template_variables
from src.app.services.email_template_service import (
    DEFAULT_TEMPLATES,
    TEMPLATE_VARIABLES,
    EmailTemplateService,
)
from src.app.services import sms_template_service as sms_svc
from src.app.tasks.notifications import (
    _extract_nopms_request_fields,
    _yes_no,
)

# The real no-PMS agent's custom_analysis_data schema (note trailing space on
# "Availability ", capitalized keys, "New Patient?" with a question mark).
NOPMS_CUSTOM = {
    "First Name": "Haiber",
    "Last Name": "Ali",
    "Emergency": False,
    "Availability ": "Tomorrow after 3 PM; Fri after 12 PM",
    "Date of birth": "2001-02-02",
    "New Patient?": True,
    "Call Status": "Needs booking",
}

APPT_REQUEST = EmailTemplateType.APPOINTMENT_REQUEST.value


# ── extraction ──────────────────────────────────────────────────────────────


def test_extracts_nopms_fields_despite_key_variants() -> None:
    out = _extract_nopms_request_fields(custom=NOPMS_CUSTOM, dynamic={})
    assert out["availability"] == "Tomorrow after 3 PM; Fri after 12 PM"
    assert out["new_patient"] == "Yes"
    assert out["is_emergency"] == "No"
    assert out["call_status"] == "Needs booking"


def test_extraction_never_returns_name_or_dob() -> None:
    out = _extract_nopms_request_fields(custom=NOPMS_CUSTOM, dynamic={})
    assert set(out.keys()) == {"availability", "new_patient", "is_emergency", "call_status"}


@pytest.mark.parametrize(
    "raw, expected",
    [(True, "Yes"), (False, "No"), ("true", "Yes"), ("No", "No"), ("", None), (None, None)],
)
def test_yes_no(raw, expected) -> None:
    assert _yes_no(raw) == expected


# ── PHI-free rendering ──────────────────────────────────────────────────────


def _render_all(variables: dict) -> str:
    d = DEFAULT_TEMPLATES[APPT_REQUEST]
    return (
        EmailTemplateService.render(d["subject_template"], variables)
        + EmailTemplateService.render(d["html_body"], variables)
        + EmailTemplateService.render(d["text_body"], variables)
    )


def test_email_renders_triage_fields() -> None:
    nopms = _extract_nopms_request_fields(custom=NOPMS_CUSTOM, dynamic={})
    variables = _build_template_variables(
        {
            "location_name": "Olive Tree Dental",
            "caller_phone_masked": "No phone number captured",
            "duration_seconds": 163,
            "primary_tag": "needs_booking",
            "availability": nopms["availability"],
            "new_patient": nopms["new_patient"],
            "is_emergency": nopms["is_emergency"],
            "call_status_label": nopms["call_status"],
            "appointment_patient_name": None,
        }
    )
    blob = _render_all(variables)
    assert "Tomorrow after 3 PM" in blob
    assert "Needs booking" in blob
    assert "No phone number captured" in blob


def test_email_is_phi_free_even_if_name_dob_leak_into_payload() -> None:
    # Defense in depth: even if a caller stuffs PHI into the payload, the
    # request template must not render it (it references no PHI variable).
    variables = _build_template_variables(
        {
            "location_name": "Clinic",
            "appointment_patient_name": "Haiber Ali",  # should never surface
            "availability": "Tomorrow",
            "new_patient": "Yes",
            "is_emergency": "No",
            "call_status_label": "Needs booking",
        }
    )
    blob = _render_all(variables)
    assert "Haiber" not in blob
    assert "2001-02-02" not in blob
    assert "{{" not in blob  # nothing left unrendered


def test_no_phi_variables_registered_in_request_catalog() -> None:
    keys = {v["key"] for v in TEMPLATE_VARIABLES[APPT_REQUEST]}
    assert "patient_name" not in keys
    assert "date_of_birth" not in keys
    assert {"availability", "new_patient", "is_emergency", "call_status"} <= keys


# ── template registration / separation from integrated ──────────────────────


def test_request_template_registered_and_distinct_from_confirmation() -> None:
    assert APPT_REQUEST in DEFAULT_TEMPLATES
    assert APPT_REQUEST in TEMPLATE_VARIABLES
    # Integrated confirmation template is untouched and still PHI-carrying.
    conf = DEFAULT_TEMPLATES[EmailTemplateType.APPOINTMENT_CONFIRMATION.value]
    assert "{{ patient_name }}" in conf["text_body"]


def test_sms_request_default_is_acknowledgement() -> None:
    t = SmsTemplateType.APPOINTMENT_REQUEST.value
    assert t in sms_svc.SMS_DEFAULT_TEMPLATES
    body = sms_svc.SMS_DEFAULT_TEMPLATES[t]["body"]
    # No booked date/provider/service in the request SMS.
    assert "appointment_datetime" not in body
    assert "appointment_provider" not in body
    rendered = sms_svc.SmsTemplateService.render(
        body, {"patient_name": "Jane", "location_name": "Olive Tree", "availability": ""}
    )
    assert "Olive Tree" in rendered
    assert "{{" not in rendered
