"""Appointment Slots routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.app.api.helpers import handle_nexhealth_request
from src.app.api.models import AppointmentSlotsResponse
from src.app.api.deps import get_current_active_user
from src.app.config import Settings, get_settings
from src.app.dependencies import get_nexhealth_client_dependency
from src.app.nexhealth.client import NexHealthClient

router = APIRouter(dependencies=[Depends(get_current_active_user)])


@router.get("/appointment_slots", response_model=AppointmentSlotsResponse)
async def list_appointment_slots(
    start_date: str = Query(..., description="Date string for results to start at, format YYYY-MM-DD"),
    days: int = Query(..., ge=1, description="Number of days to include, counting the start date"),
    lids: list[int] = Query(..., alias="lids[]", description="Array of Location Ids"),
    pids: list[int] = Query(..., alias="pids[]", description="Array of Provider Ids"),
    subdomain: str | None = Query(None, description="Used to scope the request to the specified institution"),
    operatory_ids: list[int] | None = Query(None, alias="operatory_ids[]", description="Specify if booking is mapped to operatory in this location"),
    appointment_type_id: int | None = Query(None, description="Specify an appointment type to filter returned slots and set slot_length"),
    slot_length: int | None = Query(None, description="Manually specify slot length in minutes. Defaults to 15 minutes"),
    slot_interval: int | None = Query(None, description="Time in minutes between returned slot start times when a contiguous opening exists"),
    overlapping_operatory_slots: bool | None = Query(False, description="Return all available slots for operatories at a given time instead of only the first found"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
    client: Annotated[NexHealthClient, Depends(get_nexhealth_client_dependency)] = None,
) -> dict[str, Any]:
    """
    View appointment slots.
    
    Provides valid start times for booking appointments. Requires subdomain and
    accepts arrays of location ids (lids) and provider ids (pids) to retrieve
    slots for multiple locations and providers at once.
    
    Key behaviors:
    - If the same time slot is available in multiple operatories, only one slot is returned
      (unless overlapping_operatory_slots is True)
    - If no slots are found, next_available_date will contain the next day with valid slots
      (within 180 days, otherwise null)
    - Slot times are always returned in the location's local timezone
    - If appointment_type_id is provided, slot_length is set to that appointment type's duration
    - If both appointment_type_id and slot_length are provided, appointment_type_id takes precedence
    """
    subdomain = subdomain or settings.nexhealth_subdomain

    if not subdomain:
        raise HTTPException(
            status_code=400,
            detail="Missing subdomain. Provide as query param or configure in settings.",
        )

    params: dict[str, Any] = {
        "subdomain": subdomain,
        "start_date": start_date,
        "days": days,
        "lids[]": lids,
        "pids[]": pids,
    }
    if operatory_ids:
        params["operatory_ids[]"] = operatory_ids
    if appointment_type_id:
        params["appointment_type_id"] = appointment_type_id
    if slot_length:
        params["slot_length"] = slot_length
    if slot_interval:
        params["slot_interval"] = slot_interval
    if overlapping_operatory_slots is not None:
        params["overlapping_operatory_slots"] = overlapping_operatory_slots

    return await handle_nexhealth_request(client, "GET", "/appointment_slots", params=params)
