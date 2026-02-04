"""Retell function handlers for NexHealth operations."""

from __future__ import annotations

import logging
from typing import Any

from src.app.api.helpers import fetch_all_pages, handle_nexhealth_request
from src.app.api.models import (
    CancelAppointmentBody,
    CancelAppointmentRequest,
    CreateAppointmentBody,
    CreateAppointmentRequest,
    CreatePatientBio,
    CreatePatientData,
    CreatePatientProvider,
    CreatePatientRequest,
)
from src.app.api.routes import appointment_slots as slot_routes
from src.app.api.routes import appointment_types as appt_type_routes
from src.app.api.routes import appointments as appt_routes
from src.app.api.routes import locations as location_routes
from src.app.api.routes import patients as patient_routes
from src.app.api.routes import providers as provider_routes
from src.app.api.routes import sikka as sikka_routes
from src.app.dependencies import get_settings
from src.app.models.audit_log import AuditAction, AuditActor
from src.app.retell.functions import register_function
from src.app.services.audit_decorator import audited

logger = logging.getLogger(__name__)


async def _get_nexhealth_client():
    """
    Get NexHealth client - tenant-aware if in call context, otherwise global.
    
    During Retell function calls, this checks for tenant context and uses
    tenant-specific credentials if available.
    """
    from src.app.dependencies import get_nexhealth_client_dependency
    from src.app.retell.functions import get_tenant_from_call_context
    from src.app.nexhealth.client import NexHealthClient
    from src.app.config import Settings, settings
    
    # Try to get tenant from call context
    tenant = await get_tenant_from_call_context()
    
    if tenant and tenant.nexhealth_api_key:
        # Create tenant-specific client
        tenant_settings = Settings(
            nexhealth_api_key=tenant.nexhealth_api_key,
            nexhealth_subdomain=tenant.nexhealth_subdomain or settings.nexhealth_subdomain,
            nexhealth_location_id=tenant.nexhealth_location_id or settings.nexhealth_location_id,
        )
        client = NexHealthClient(config=tenant_settings)
        await client.__aenter__()
        logger.info(f"Using tenant-specific NexHealth client for '{tenant.slug}'")
        return client
    
    # Fall back to global client
    return await get_nexhealth_client_dependency()


async def _get_sikka_client():
    """Get the global Sikka client."""
    from src.app.dependencies import get_sikka_client_dependency
    return await get_sikka_client_dependency()


# ============================================================================
# Patient Functions
# ============================================================================


@register_function("lookup_patient")
@audited(AuditAction.READ_PATIENT, resource_key="patient_id")
async def lookup_patient(args: dict[str, Any]) -> dict[str, Any]:
    """
    Lookup a patient by name, email, phone, or date of birth.
    
    Args:
        args:
            - name: Patient name
            - email: Patient email
            - phone_number: Patient phone number
            - date_of_birth: Patient DOB (YYYY-MM-DD)
            - subdomain: Institution subdomain (optional, NexHealth only)
            - location_id: Location ID (optional, NexHealth only)
            - pms_provider: 'nexhealth' (default) or 'sikka'
            - office_id: Practice Office ID (Sikka only)
            - secret_key: Practice Secret Key (Sikka only)
    """
    settings = get_settings()
    pms_provider = args.get("pms_provider", "nexhealth").lower()
    
    # -------------------------------------------------------------------------
    # Sikka Logic
    # -------------------------------------------------------------------------
    if pms_provider == "sikka":
        client = await _get_sikka_client()
        if not client:
            return {"message": "Sikka integration is not enabled on this server."}
            
        office_id = args.get("office_id")
        secret_key = args.get("secret_key")
        if not office_id or not secret_key:
             return {"message": "I need the practice Office ID and Secret Key to access Sikka records."}

        # Filter args for Sikka (firstname, lastname, cell, email, etc.)
        # Retell 'name' -> split to firstname/lastname? Or use 'search' param.
        # Sikka 'search' param covers firstname, lastname, email, phone.
        
        search_term = args.get("name") or args.get("email") or args.get("phone_number")
        
        try:
            # We use the 'search' param for broad matching if specific fields aren't perfect
            # Or map specific fields if we want precision.
            # Sikka route supports: firstname, lastname, cell, email, search
            
            # Simple approach: If phone provided, use 'cell' (best match). 
            # If name provided, use 'search'.
            
            cell = args.get("phone_number")
            email = args.get("email")
            
            response_model = await sikka_routes.list_patients(
                client=client,
                office_id=office_id,
                secret_key=secret_key,
                cell=cell,
                email=email,
                search=search_term if not cell and not email else None,
                page=1,
                per_page=5
            )
            
            patients = response_model.patients  # SikkaPatientListResponse.patients (list of dicts)
            count = response_model.count
            
            if count == 0:
                return {"message": "No patients found in Sikka matching the criteria."}
                
            simplified_patients = []
            for p in patients:
                # Sikka patient dict structure (from manual test):
                # { "patient_id": "33", "firstname": "test", "lastname": "reward", "email": ..., "cell": ... }
                
                simplified_patients.append({
                    "id": p.get("patient_id"),
                    "first_name": p.get("firstname"),
                    "last_name": p.get("lastname"),
                    "email": p.get("email"),
                    "phone_number": p.get("cell"),
                    "date_of_birth": p.get("birthdate"), # Format might be ISO datetime "1990-01-01T00:00:00"
                    "source": "sikka"
                })
            
            return {
                "count": count,
                "patients": simplified_patients,
                "message": f"Found {count} patient(s) in Sikka."
            }
            
        except Exception as e:
            logger.error(f"Sikka patient lookup failed: {e}")
            return {"message": f"I had trouble accessing Sikka records: {str(e)}"}

    # -------------------------------------------------------------------------
    # NexHealth Logic (Default)
    # -------------------------------------------------------------------------
    
    client = await _get_nexhealth_client()
    
    # Filter args to valid params
    valid_params = ["name", "email", "phone_number", "date_of_birth", "subdomain", "location_id"]
    cleaned_args = {k: v for k, v in args.items() if k in valid_params and v}
    
    if not cleaned_args:
        return {"message": "Please provide at least one search criterion (name, email, phone, or DOB)."}

    # Validation: Must have location_id and subdomain (Voice specific constraint)
    if not cleaned_args.get("location_id") or not cleaned_args.get("subdomain"):
        return {"message": "I need to know which practice location and subdomain you are calling about specificially. Please list locations first."}
    
    try:
        # Call Route directly
        response_model = await patient_routes.list_patients(
            subdomain=cleaned_args.get("subdomain"),
            location_id=cleaned_args.get("location_id"),
            name=cleaned_args.get("name"),
            email=cleaned_args.get("email"),
            phone_number=cleaned_args.get("phone_number"),
            date_of_birth=cleaned_args.get("date_of_birth"),
            page=1,
            per_page=5,
            settings=settings,
            client=client,
            inactive=None,
            foreign_id=None,
            updated_since=None,
            new_patient=None,
            non_patient=None,
            forms_syncable=None,
            location_strict=None,
            include=["upcoming_appts", "last_visited_appointment", "procedures"],  # Include appointment and procedure history
            sort=None,
            appointment_date_start=None,
            appointment_date_end=None
        )
    except Exception as e:
        logger.error(f"Patient lookup failed: {e}")
        return {"message": "I had trouble accessing the patient records. Please verify the location and try again."}

    # Extract data from Pydantic model response
    # list_patients returns PatientListResponse dict (because return type hint is dict[str, Any] but implementation returns dict from handle_nexhealth_request)
    # The route returns the helper result which is a dict.
    
    data = response_model.get("data", {})
    patients = data.get("patients", [])
    count = len(patients)
    
    if count == 0:
        return {"message": "No patients found matching the criteria."}
    
    # Minimize data returned to LLM context
    simplified_patients = []
    for p in patients:
        # p is a dict
        # Extract appointment history
        upcoming_appts = p.get("upcoming_appts", [])
        last_visited = p.get("last_visited_appointment")
        procedures = p.get("procedures", [])
        
        simplified_patients.append({
            "id": p.get("id"),
            "first_name": p.get("first_name"),
            "last_name": p.get("last_name"),
            "email": p.get("email"),
            "phone_number": p.get("phone_number"),
            "date_of_birth": p.get("date_of_birth") or p.get("bio", {}).get("date_of_birth"),
            "upcoming_appointments": [{
                "id": appt.get("id"),
                "provider_id": appt.get("provider_id"),
                "provider_name": appt.get("provider_name"),
                "start_time": appt.get("start_time"),
                "location_id": appt.get("location_id")
            } for appt in upcoming_appts[:3]],  # Limit to 3 most recent
            "last_visit": {
                "id": last_visited.get("id"),
                "provider_id": last_visited.get("provider_id"),
                "provider_name": last_visited.get("provider_name"),
                "start_time": last_visited.get("start_time"),
                "location_id": last_visited.get("location_id")
            } if last_visited else None,
            "recent_procedures": [{
                "id": proc.get("id"),
                "code": proc.get("code"),
                "name": proc.get("name"),
                "status": proc.get("status"),
                "date": proc.get("start_date")
            } for proc in procedures[:5]]  # Limit to 5 most recent procedures
        })

    return {
        "count": count,
        "patients": simplified_patients,
        "message": f"Found {count} patient(s)."
    }


@register_function("create_patient")
@audited(AuditAction.CREATE_PATIENT, resource_from_result="patient_id")
async def create_patient(args: dict[str, Any]) -> dict[str, Any]:
    """
    Create a new patient.

    Args:
        args:
            - first_name: First Name (required)
            - last_name: Last Name (required)
            - email: Email (required)
            - phone_number: Phone Number (required)
            - date_of_birth: Date of Birth YYYY-MM-DD (required)
            - location_id: Location ID (required)
            - subdomain: Subdomain (required)
            - provider_id: Provider ID (required)
    """
    required_fields = ["first_name", "last_name", "email", "phone_number", "date_of_birth", "location_id", "subdomain", "provider_id"]
    for field in required_fields:
        if not args.get(field):
            return {"error": f"{field} is required."}

    # Create Request models
    bio_data = CreatePatientBio(
        date_of_birth=args.get("date_of_birth"),
        phone_number=args.get("phone_number"),
        gender=args.get("gender", "Female")
    )
    
    patient_data = CreatePatientData(
        first_name=args.get("first_name"),
        last_name=args.get("last_name"),
        email=args.get("email"),
        bio=bio_data
    )
    
    provider_data = CreatePatientProvider(
        provider_id=args.get("provider_id")
    )
    
    request_body = CreatePatientRequest(
        provider=provider_data,
        patient=patient_data
    )

    client = await _get_nexhealth_client()
    settings = get_settings()

    try:
        response = await patient_routes.create_patient(
            body=request_body,
            subdomain=args.get("subdomain"),
            location_id=args.get("location_id"),
            settings=settings,
            client=client
        )
        
        # Parse Response
        if response.get("code") is False:
             return {
                "success": False,
                "error": response.get("error") or "Failed to create patient",
            }
            
        data = response.get("data", {}).get("user", {})
        return {
            "success": True,
            "patient_id": data.get("id"),
            "message": f"Patient {data.get('first_name')} created successfully."
        }
    except Exception as e:
        logger.error(f"Failed to create patient: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# Available Slots Functions
# ============================================================================


@register_function("find_appointment_slots")
async def find_appointment_slots(args: dict[str, Any]) -> dict[str, Any]:
    """
    Find available appointment slots.

    Args:
        args:
            - start_date: Date to start searching (YYYY-MM-DD) (required)
            - days: Number of days to look ahead (default 7)
            - location_id: Location ID (required)
            - provider_id: Provider ID (optional)
            - appointment_type_id: Appointment Type ID (optional)
            - subdomain: Institution subdomain (optional)
            - operatory_ids: List of operatory IDs (optional)
    """
    start_date = args.get("start_date")
    location_id = args.get("location_id")
    subdomain = args.get("subdomain")

    if not start_date or not location_id or not subdomain:
        return {"error": "start_date, location_id, and subdomain are required."}

    client = await _get_nexhealth_client()
    settings = get_settings()

    pids = []
    if args.get("provider_id"):
        pids = [args.get("provider_id")]
    else:
        # Auto-fetch ALL providers for the location using pagination
        try:
            async def fetch_providers(page: int, per_page: int) -> dict[str, Any]:
                return await handle_nexhealth_request(
                    client,
                    "GET",
                    "/providers",
                    params={
                        "subdomain": subdomain,
                        "location_id": location_id,
                        "page": page,
                        "per_page": per_page,
                    },
                )

            all_providers = await fetch_all_pages(fetch_providers, per_page=50, max_items=200)
            pids = [p["id"] for p in all_providers if "id" in p]
            logger.info(f"Auto-fetched {len(pids)} provider IDs for slot search")
        except Exception as e:
            logger.warning(f"Failed to auto-fetch providers for slot search: {e}")

    try:
        response = await slot_routes.list_appointment_slots(
            start_date=start_date,
            days=args.get("days", 7),
            lids=[location_id],
            pids=pids,
            subdomain=args.get("subdomain"),
            operatory_ids=args.get("operatory_ids"),
            appointment_type_id=args.get("appointment_type_id"),
            settings=settings,
            client=client,
            slot_length=None,
            slot_interval=None,
            overlapping_operatory_slots=False
        )

        slots = response.get("data", [])

        return {
            "slots_count": len(slots),
            "slots": slots,
            "message": f"Found {len(slots)} available slot(s)."
        }
    except Exception as e:
        logger.error(f"Failed to find appointment slots: {e}")
        return {"error": f"Failed to find slots: {str(e)}"}


# ============================================================================
# Appointment Functions
# ============================================================================


@register_function("book_appointment")
@audited(AuditAction.BOOK_APPOINTMENT, resource_from_result="appointment_id")
async def book_appointment(args: dict[str, Any]) -> dict[str, Any]:
    """
    Book a new appointment.

    Args:
        args:
            - subdomain: Institution subdomain (required)
            - location_id: Location ID (required)
            - patient_id: Patient ID (required)
            - provider_id: Provider ID (required)
            - operatory_id: Operatory ID (optional but often required by practice)
            - start_time: Start time (ISO 8601) (required)
            - end_time: End time (ISO 8601) (optional)
            - appointment_type_id: Appointment Type ID (optional)
            - descriptor_ids: List of EHR descriptor IDs for PMS sync (optional)
            - note: Note for the appointment (optional, max 128 chars)
    """
    logger.info(f"Book Appointment Args: {args}")
    required_fields = ["subdomain", "location_id", "patient_id", "provider_id", "start_time"]
    for field in required_fields:
        if not args.get(field):
            return {"error": f"{field} is required."}

    client = await _get_nexhealth_client()
    settings = get_settings()

    # Create Request model
    appt_body = CreateAppointmentBody(
        patient_id=args.get("patient_id"),
        provider_id=args.get("provider_id"),
        start_time=args.get("start_time"),
        operatory_id=args.get("operatory_id"),
        end_time=args.get("end_time"),
        appointment_type_id=args.get("appointment_type_id"),
        descriptor_ids=args.get("descriptor_ids"),  # EHR procedure codes for PMS sync
        note=args.get("note"),
        referrer=args.get("referrer"),
    )
    request_body = CreateAppointmentRequest(appt=appt_body)
    
    try:
        response = await appt_routes.book_appointment(
            body=request_body,
            subdomain=args.get("subdomain"),
            location_id=args.get("location_id"),
            notify_patient=True,
            settings=settings,
            client=client
        )
        
        # Check for error in response data logic if client doesn't raise exception
        if response.get("code") is False or response.get("error"):
             return {
                "success": False,
                "error": response.get("error") or response.get("description") or "Unknown error",
                "details": response
            }
            
        data = response.get("data", {}).get("appt", {})
        return {
            "success": True,
            "appointment_id": data.get("id"),
            "start_time": data.get("start_time"),
            "message": "Appointment booked successfully."
        }
    except Exception as e:
        logger.error(f"Failed to book appointment: {e}")
        return {"success": False, "error": str(e)}


@register_function("cancel_appointment")
@audited(AuditAction.CANCEL_APPOINTMENT, resource_key="appointment_id")
async def cancel_appointment(args: dict[str, Any]) -> dict[str, Any]:
    """
    Cancel an existing appointment.
    
    Args:
        args:
            - appointment_id: ID of appointment to cancel (required)
            - subdomain: Institution subdomain (required)
    """
    appointment_id = args.get("appointment_id")
    subdomain = args.get("subdomain")
    
    if not appointment_id or not subdomain:
        return {"error": "appointment_id and subdomain are required."}
        
    client = await _get_nexhealth_client()
    settings = get_settings()
    
    cancel_body = CancelAppointmentBody(cancelled=True)
    request_body = CancelAppointmentRequest(appt=cancel_body)
    
    try:
        response = await appt_routes.cancel_appointment(
            id=appointment_id,
            body=request_body,
            subdomain=subdomain,
            settings=settings,
            client=client
        )
        
        if response.get("code") is False:
             return {
                "success": False,
                "error": response.get("error") or "Failed to cancel",
            }
            
        return {
            "success": True,
            "message": "Appointment cancelled successfully."
        }
    except Exception as e:
        logger.error(f"Failed to cancel appointment: {e}")
        return {"success": False, "error": str(e)}


@register_function("reschedule_appointment")
@audited(AuditAction.RESCHEDULE_APPOINTMENT, resource_key="old_appointment_id")
async def reschedule_appointment(args: dict[str, Any]) -> dict[str, Any]:
    """
    Reschedule an appointment by cancelling the old one and booking a new one.
    
    Args:
        args:
            - old_appointment_id: ID of appointment to cancel (required)
            - subdomain: Institution subdomain (required)
            - location_id: Location ID (required for new booking)
            - patient_id: Patient ID (required for new booking)
            - provider_id: Provider ID (required for new booking)
            - start_time: New start time (required)
            - ... (other booking args)
    """
    # 1. Cancel old appointment
    cancel_args = {
        "appointment_id": args.get("old_appointment_id"),
        "subdomain": args.get("subdomain")
    }
    
    cancel_result = await cancel_appointment(cancel_args)
    if not cancel_result.get("success") and not "already cancelled" in str(cancel_result.get("error", "")).lower():
         # If cancel failed and it wasn't because it was already cancelled, abort
         return {
             "success": False, 
             "error": f"Failed to cancel old appointment: {cancel_result.get('error')}"
         }
         
    # 2. Book new appointment
    # Filter args for booking
    book_result = await book_appointment(args)
    
    if book_result.get("success"):
        book_result["message"] = "Rescheduled successfully (old appointment cancelled, new one booked)."
        return book_result
    else:
        return {
            "success": False,
            "error": f"Old appointment cancelled, but failed to book new one: {book_result.get('error')}"
        }


# ============================================================================
# Info / FAQ Functions
# ============================================================================


@register_function("list_appointment_types")
async def list_appointment_types(args: dict[str, Any]) -> dict[str, Any]:
    """
    List appointment types for a practice.
    
    Args:
        args:
            - subdomain: Institution subdomain (required)
            - location_id: Location ID (optional)
    """
    subdomain = args.get("subdomain")
    location_id = args.get("location_id")
    if not subdomain:
        return {"error": "subdomain is required."}
        
    client = await _get_nexhealth_client()
    settings = get_settings()

    try:
        response = await appt_type_routes.list_appointment_types(
            subdomain=subdomain,
            location_id=location_id,
            include=["descriptors"],
            settings=settings,
            client=client
        )
        
        data = response.get("data", [])
        simplified_types = []
        for at in data:
            # Extract descriptor IDs
            descriptors = at.get("descriptors", [])
            descriptor_ids = [d.get("id") for d in descriptors if d.get("id")]
            
            simplified_types.append({
                "id": at.get("id"),
                "name": at.get("name"),
                "minutes": at.get("minutes"),
                "descriptor_ids": descriptor_ids
            })
            
        return {
            "count": len(simplified_types),
            "appointment_types": simplified_types,
            "message": f"Found {len(simplified_types)} appointment types."
        }
    except Exception as e:
        logger.error(f"Failed to list appointment types: {e}")
        return {"error": str(e)}


@register_function("get_location_details")
async def get_location_details(args: dict[str, Any]) -> dict[str, Any]:
    """
    Get location details for FAQs (hours, address, etc).
    
    Args:
        args:
            - location_id: Location ID (required)
    """
    location_id = args.get("location_id")
    if not location_id:
        return {"error": "location_id is required."}
        
    client = await _get_nexhealth_client()

    try:
        response_model = await location_routes.get_location(
            location_id=location_id,
            client=client
        )
        # response_model is LocationDetailResponse, so we model_dump it or access .data
        # Actually handle_nexhealth_request returns dict usually if not parsed by response_model in direct call?
        # Fastapi returns the model, but the function returns what handle_nexhealth_request returns which is dict.
        # Let's check handle_nexhealth_request return type. It is dict[str, Any].
        # So we treat it as dict.
        
        location = response_model.get("data", {})
        
        # Return relevant info for voice agent
        info = {
            "name": location.get("name"),
            "address": location.get("address"), 
            "phone": location.get("phone"),
            "hours": location.get("hours"),
            "timezone": location.get("timezone")
        }
        
        return {
            "practice_name": location.get("name"),
            "location": info,
            "full_details": location 
        }
    except Exception as e:
        logger.error(f"Failed to get location details: {e}")
        return {"error": f"Failed to retrieve location details: {str(e)}"}


@register_function("list_locations")
async def list_locations(args: dict[str, Any]) -> dict[str, Any]:
    """
    List all available practice locations.
    
    Args:
        args: {} (No arguments required)
    """
    client = await _get_nexhealth_client()
    
    try:
        response_model = await location_routes.list_locations(
            client=client,
            subdomain=None,
            inactive=None,
            foreign_id=None,
            filter_by_subscription_feature=None,
            page=1,
            per_page=25
        )
        data = response_model.get("data", [])
        
        all_locations = []
        if isinstance(data, list):
            for inst in data:
                # If inst has 'locations' key
                locs = inst.get("locations", [])
                for loc in locs:
                    all_locations.append({
                        "id": loc.get("id"),
                        "name": loc.get("name"),
                        "subdomain": inst.get("subdomain"), # Important context
                        "address": loc.get("street_address"),
                        "city": loc.get("city")
                    })
        
        return {
            "count": len(all_locations),
            "locations": all_locations,
            "message": f"Found {len(all_locations)} location(s)."
        }
            
    except Exception as e:
        logger.error(f"Failed to list locations: {e}")
        return {"error": f"Failed to list locations: {str(e)}"}



@register_function("list_providers")
async def list_providers(args: dict[str, Any]) -> dict[str, Any]:
    """
    List ALL providers at a specific location.

    Uses internal pagination to fetch all providers and return combined simplified list.

    Args:
        args:
            - location_id: Location ID (required)
            - subdomain: Institution subdomain (required)
    """
    location_id = args.get("location_id")
    subdomain = args.get("subdomain")
    if not location_id or not subdomain:
        return {"error": "location_id and subdomain are required to list providers."}

    client = await _get_nexhealth_client()

    try:
        # Define fetch function for pagination helper
        async def fetch_providers(page: int, per_page: int) -> dict[str, Any]:
            return await handle_nexhealth_request(
                client,
                "GET",
                "/providers",
                params={
                    "subdomain": subdomain,
                    "location_id": location_id,
                    "page": page,
                    "per_page": per_page,
                    "include[]": ["availabilities", "appointment_types"],
                },
            )

        # Fetch all providers using pagination helper
        all_providers = await fetch_all_pages(fetch_providers, per_page=50, max_items=200)

        logger.info(f"Fetched {len(all_providers)} total providers for location {location_id}")

        # Simplify provider data for voice agent
        simplified_providers = []
        for p in all_providers:
            # Extract appointment types from availabilities
            appointment_types = []
            operatory_ids = []
            availabilities = p.get("availabilities", [])
            for avail in availabilities:
                # Extract operatory ID if present
                op_id = avail.get("operatory_id")
                if op_id and op_id not in operatory_ids:
                    operatory_ids.append(op_id)

                # Extract appointment types
                appt_types = avail.get("appointment_types", [])
                for apt in appt_types:
                    # Only add unique appointment types
                    apt_id = apt.get("id")
                    if apt_id and not any(at.get("id") == apt_id for at in appointment_types):
                        appointment_types.append({
                            "id": apt.get("id"),
                            "name": apt.get("name"),
                            "minutes": apt.get("minutes"),
                            "bookable_online": apt.get("bookable_online"),
                        })

            simplified_providers.append({
                "id": p.get("id"),
                "name": p.get("name"),
                "first_name": p.get("first_name"),
                "last_name": p.get("last_name"),
                "specialty": p.get("nexhealth_specialty"),
                "appointment_types": appointment_types,
                "operatory_ids": operatory_ids,
            })

        return {
            "count": len(simplified_providers),
            "providers": simplified_providers,
            "message": f"Found {len(simplified_providers)} provider(s).",
        }
    except Exception as e:
        logger.error(f"Failed to list providers: {e}")
        return {"error": f"Failed to list providers: {str(e)}"}


@register_function("list_operatories")
async def list_operatories(args: dict[str, Any]) -> dict[str, Any]:
    """
    List operatories (chairs/rooms) at a specific location.
    
    Args:
        args:
            - location_id: Location ID (required)
            - subdomain: Institution subdomain (optional)
    """
    location_id = args.get("location_id")
    subdomain = args.get("subdomain")
    
    if not location_id or not subdomain:
        return {"error": "location_id and subdomain are required to list operatories."}
        
    client = await _get_nexhealth_client()
    settings = get_settings()

    try:
        # Import internally to avoid circular imports if any, though usually routes are safe
        from src.app.api.routes import operatories as operatory_routes
        
        response_model = await operatory_routes.list_operatories(
            location_id=location_id,
            subdomain=subdomain,
            page=1, 
            per_page=10,
            settings=settings,
            client=client
        )
        
        data = response_model.get("data", [])
        
        simplified_operatories = []
        for op in data:
            simplified_operatories.append({
                "id": op.get("id"),
                "name": op.get("name"),
                "active": op.get("active")
            })
            
        return {
            "count": len(simplified_operatories),
            "operatories": simplified_operatories,
            "message": f"Found {len(simplified_operatories)} operatories."
        }
    except Exception as e:
        logger.error(f"Failed to list operatories: {e}")
        return {"error": f"Failed to list operatories: {str(e)}"}
