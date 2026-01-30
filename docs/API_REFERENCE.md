# Voice Agent API Reference

This backend provides a streamlined wrapper around the NexHealth Synchronizer API, optimized for a HIPAA-compliant voice agent.

## Base URL
Defaults to `/api/v1/nexhealth`.

## Authentication
All endpoints require the standard Service-to-Service authentication configured for the Voice Agent.

---

## Appointments

### List Appointments
`GET /appointments`

Retrieve a list of appointments. Useful for finding existing bookings to reschedule or cancel.

**Parameters:**
- `subdomain` (optional): Scope to institution.
- `location_id` (optional): Filter by location.
- `provider_id` (optional): Filter by provider.
- `start_date` (optional): Filter by date (ISO8601).
- `end_date` (optional): Filter by date (ISO8601).

### Get Appointment
`GET /appointments/{id}`

Retrieve details for a single appointment.

---

## Appointment Slots (Availability)

### Get Slots
`GET /appointment_slots`

Find available time slots for booking.

**Parameters:**
- `start_date` (required): YYYY-MM-DD.
- `days` (required): Number of days to look ahead.
- `lids[]` (required): List of Location IDs.
- `pids[]` (required): List of Provider IDs.
- `appointment_type_id` (optional): Duration filter.

**Response:**
Returns a list of `AvailableSlotResponse` objects containing valid start times.

---

## Patients

### List Patients
`GET /patients`

Lookup patients by name or other criteria.

**Parameters:**
- `search` (optional): Search term (name, email, phone).
- `first_name` (optional).
- `last_name` (optional).
- `email` (optional).
- `phone_number` (optional).

### Get Patient
`GET /patients/{id}`

Retrieve a patient's details, including:
- Basic info (Name, DOB).
- Insurance Coverages.
- Upcoming Appointments.
- Procedures (if available).
- Guarantor (if available).

---

## Providers

### List Providers
`GET /providers`

List available providers (doctors/hygienists).

### Get Provider
`GET /providers/{id}`

Retrieve a specific provider's details.

---

## Appointment Types

### List Appointment Types
`GET /appointment_types`

List reasons for visit (e.g., "New Patient Exam").

### Get Appointment Type
`GET /appointment_types/{id}`

---

## Locations

### List Locations
`GET /locations`

List physical practice locations. Use this to get `location_id` and `subdomain` for other queries.

### Get Location
`GET /locations/{id}`

Retrieve location details (address, timezone).

---

## Disabled / Internal APIs
The following NexHealth resources are **disabled** or not exposed to the Voice Agent:
- `Institutions`
- `Availabilities`
- `Operatories`
