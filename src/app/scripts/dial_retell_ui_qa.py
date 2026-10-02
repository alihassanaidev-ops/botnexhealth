"""Trigger the stable UI-visible Retell browser-phone QA campaign.

Local/manual QA helper. It enqueues one workflow run for the existing Downtown
Retell Browser QA campaign so the run appears in the Campaigns UI.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.app.tasks.automation_workflow import enroll_and_start_workflow_run


INSTITUTION_ID = "66b77a28-f968-4c77-a551-e14939bdff61"
LOCATION_ID = "5fb67239-e8e5-4d94-bdc3-3d372540bc7a"
WORKFLOW_ID = "67bb2aaf-a12a-4e4d-afb6-f0f51c7a509c"
WORKFLOW_VERSION_ID = "fd736686-87a0-44fd-ac54-aa236b714d35"
CONTACT_ID = "d13f2859-0dd0-4f14-a61d-c61980154b56"


def main() -> None:
    key = "ui-stable-retell-demo-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    result = enroll_and_start_workflow_run.apply_async(
        kwargs={
            "institution_id": INSTITUTION_ID,
            "workflow_id": WORKFLOW_ID,
            "workflow_version_id": WORKFLOW_VERSION_ID,
            "contact_id": CONTACT_ID,
            "location_id": LOCATION_ID,
            "trigger_type": "manual_qa",
            "trigger_ref_type": "demo",
            "trigger_ref_id": "browser-phone-ui-demo",
            "idempotency_key": key,
            "trigger_metadata": {
                "appointment_id": "ui-demo-appointment",
                "appointment_at": "2026-07-28T10:00:00+00:00",
                "appointment_datetime": "2026-07-28T10:00:00+00:00",
                "appointment_date": "July 28, 2026",
                "appointment_time": "10:00 AM",
                "appointment_status": "scheduled",
                "patient_id": "ui-demo-patient",
                "provider_id": "ui-demo-provider",
                "campaign_goal": "pre_appointment_confirmation",
                "qa_reason": "stable_ui_campaign_manual_redial",
            },
        },
        queue="workflow",
    )
    print("task_id=", result.id)
    print("idempotency_key=", key)


if __name__ == "__main__":
    main()
