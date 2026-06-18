"""No-PMS call-status vocabulary normalization.

No-PMS agents emit request-style statuses; these must normalize to the right
stored values — and "Needs call back" must fold into needs_callback so it still
lands in the Callback Queue.
"""

from __future__ import annotations

from src.app.models.call import CallStatus
from src.app.services.post_call_service import RETELL_STATUS_MAP, PostCallService


def test_no_pms_status_tokens_map_to_expected_values() -> None:
    assert RETELL_STATUS_MAP["needs booking"] == CallStatus.NEEDS_BOOKING.value
    assert RETELL_STATUS_MAP["needs reschedule"] == CallStatus.NEEDS_RESCHEDULE.value
    assert RETELL_STATUS_MAP["needs cancellation"] == CallStatus.NEEDS_CANCELLATION.value
    assert RETELL_STATUS_MAP["insurance and billing"] == CallStatus.INSURANCE_AND_BILLING.value
    # Folds into the existing callback status so the Callback Queue still works.
    assert RETELL_STATUS_MAP["needs call back"] == CallStatus.NEEDS_CALLBACK.value
    # "Financial" reuses the PMS financial concept.
    assert RETELL_STATUS_MAP["financial"] == CallStatus.FINANCIAL_INQUIRY.value


def test_parse_call_tags_handles_no_pms_csv() -> None:
    # _parse_call_tags doesn't use instance state; skip __init__.
    svc = PostCallService.__new__(PostCallService)
    primary, csv = svc._parse_call_tags({"Call Status": "Needs booking, Insurance and Billing"})
    assert primary == CallStatus.NEEDS_BOOKING.value
    assert csv == f"{CallStatus.NEEDS_BOOKING.value},{CallStatus.INSURANCE_AND_BILLING.value}"

    # "Needs call back" alone → needs_callback (Callback Queue).
    primary2, _ = svc._parse_call_tags({"Call Status": "Needs call back"})
    assert primary2 == CallStatus.NEEDS_CALLBACK.value
