# Retell Function Schemas

Copy these JSON schemas into the **Parameters** section for each Custom Function in your Retell Dashboard.

**Note:** Update the **API Endpoint** for each function to:
`https://<your-ngrok-url>/api/v1/retell/functions?name=<FUNCTION_NAME>`

---

## 9. List Providers
**Function Name:** `list_providers`
**Description:** List doctors/providers at a specific practice location.

```json
{
  "type": "object",
  "properties": {
    "location_id": {
      "type": "integer",
      "description": "Location ID to find providers for."
    },
    "subdomain": {
      "type": "string",
      "description": "Institution subdomain."
    }
  },
  "required": [
    "location_id",
    "subdomain"
  ]
}
```

**Response Variables:**
- `found_providers` = `providers`
- `provider_count` = `count`

---

## 1. Lookup Patient
**Function Name:** `lookup_patient`
**Description:** Search for an existing patient record.

```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "description": "Full name of the patient."
    },
    "date_of_birth": {
      "type": "string",
      "description": "YYYY-MM-DD",
      "format": "date"
    },
    "phone_number": {
      "type": "string",
      "description": "Patient phone number."
    },
    "email": {
      "type": "string",
      "description": "Patient email."
    },
    "location_id": {
      "type": "integer",
      "description": "Location ID context."
    },
    "subdomain": {
      "type": "string",
      "description": "Institution subdomain."
    }
  },
  "required": [
    "location_id",
    "subdomain"
  ]
}

```

**Response Variables:**
- `found_patients` = `patients`
- `patient_count` = `count`

---

## 2. Create Patient
**Function Name:** `create_patient`
**Description:** Create a new patient record.

```json
{
  "type": "object",
  "properties": {
    "first_name": {
      "type": "string",
      "description": "Patient first name."
    },
    "last_name": {
      "type": "string",
      "description": "Patient last name."
    },
    "email": {
      "type": "string",
      "description": "Patient email."
    },
    "phone_number": {
      "type": "string",
      "description": "Patient phone number."
    },
    "date_of_birth": {
      "type": "string",
      "description": "YYYY-MM-DD",
      "format": "date"
    },
    "location_id": {
      "type": "integer",
      "description": "Location ID."
    },
    "subdomain": {
      "type": "string",
      "description": "Institution subdomain."
    },
    "provider_id": {
      "type": "integer",
      "description": "Provider ID context."
    }
  },
  "required": [
    "first_name",
    "last_name",
    "email",
    "phone_number",
    "date_of_birth",
    "location_id",
    "subdomain",
    "provider_id"
  ]
}
```

**Response Variables:**
- `new_patient_id` = `patient_id`
- `creation_success` = `success`

---

## 3. Check Availability
**Function Name:** `find_appointment_slots`
**Description:** Find available time slots for appointments.

```json
{
  "type": "object",
  "properties": {
    "start_date": {
      "type": "string",
      "description": "Start date (YYYY-MM-DD).",
      "format": "date"
    },
    "location_id": {
      "type": "integer",
      "description": "Location ID."
    },
    "days": {
      "type": "integer",
      "description": "Number of days to search.",
      "default": 3
    },
    "provider_id": {
      "type": "integer",
      "description": "Specific provider ID (optional)."
    },
    "appointment_type_id": {
      "type": "integer",
      "description": "Appointment Type ID (optional)."
    },
    "subdomain": {
      "type": "string",
      "description": "Institution subdomain."
    }
  },
  "required": [
    "start_date",
    "location_id",
    "subdomain"
  ]
}

```

**Response Variables:**
- `available_slots_count` = `slots_count`

---

## 4. Book Appointment
**Function Name:** `book_appointment`
**Description:** Book a new appointment.

```json
{
  "type": "object",
  "properties": {
    "location_id": {
      "type": "integer",
      "description": "Location ID."
    },
    "patient_id": {
      "type": "integer",
      "description": "Patient ID."
    },
    "provider_id": {
      "type": "integer",
      "description": "Provider ID."
    },
    "start_time": {
      "type": "string",
      "description": "ISO 8601 Timestamp (e.g. 2023-10-27T09:00:00).",
      "format": "date-time"
    },
    "operatory_id": {
      "type": "integer",
      "description": "Operatory ID (Required for this practice)."
    },
    "note": {
      "type": "string",
      "description": "Appointment note."
    },
    "subdomain": {
      "type": "string",
      "description": "Institution subdomain."
    }
  },
  "required": [
    "location_id",
    "patient_id",
    "provider_id",
    "operatory_id",
    "start_time",
    "subdomain"
  ]
}
```

**Response Variables:**
- `booked_appointment_id` = `appointment_id`
- `booking_success` = `success`

---

## 5. Cancel Appointment
**Function Name:** `cancel_appointment`
**Description:** Cancel an upcoming appointment.

```json
{
  "type": "object",
  "properties": {
    "appointment_id": {
      "type": "integer",
      "description": "ID of the appointment to cancel."
    },
    "subdomain": {
      "type": "string",
      "description": "Institution subdomain."
    }
  },
  "required": [
    "appointment_id",
    "subdomain"
  ]
}
```

**Response Variables:**
- `cancellation_success` = `success`

---

## 6. Reschedule Appointment
**Function Name:** `reschedule_appointment`
**Description:** Cancel an old appointment and book a new one.

```json
{
  "type": "object",
  "properties": {
    "old_appointment_id": {
      "type": "integer",
      "description": "ID of the appointment to cancel."
    },
    "location_id": {
      "type": "integer",
      "description": "Location ID for new appointment."
    },
    "patient_id": {
      "type": "integer",
      "description": "Patient ID."
    },
    "provider_id": {
      "type": "integer",
      "description": "Provider ID."
    },
    "start_time": {
      "type": "string",
      "description": "New ISO 8601 start time."
    },
    "operatory_id": {
      "type": "integer",
      "description": "Operatory ID (Required for this practice)."
    },
    "subdomain": {
      "type": "string",
      "description": "Institution subdomain."
    }
  },
  "required": [
    "old_appointment_id",
    "location_id",
    "patient_id",
    "provider_id",
    "operatory_id",
    "start_time",
    "subdomain"
  ]
}
```

---

## 7. Get Location Details
**Function Name:** `get_location_details`
**Description:** Get address, hours, and info for a location.

```json
{
  "type": "object",
  "properties": {
    "location_id": {
      "type": "integer",
      "description": "Location ID to lookup."
    }
  },
  "required": [
    "location_id"
  ]
}
```

**Response Variables:**
- `practice_hours` = `location.hours`
- `practice_name` = `practice_name`

---

## 8. List Locations
**Function Name:** `list_locations`
**Description:** List all practice locations to find IDs and names.

```json
{
  "type": "object",
  "properties": {},
  "required": []
}
```

**Response Variables:**
- `found_locations_count` = `count`
- `found_locations` = `locations`

---

## 9. List Operatories
**Function Name:** `list_operatories`
**Description:** List operatories (chairs/rooms) at a specific practice location.

```json
{
  "type": "object",
  "properties": {
    "location_id": {
      "type": "integer",
      "description": "Location ID to find operatories for."
    },
    "subdomain": {
      "type": "string",
      "description": "Institution subdomain."
    }
  },
  "required": [
    "location_id",
    "subdomain"
  ]
}
```

**Response Variables:**
- `found_operatories` = `operatories`
- `operatory_count` = `count`
