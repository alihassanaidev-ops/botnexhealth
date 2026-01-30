# Implementation Plan - Create Patient for Retell

The goal is to implement the `create_patient` function in the Retell handler to allow the voice agent to register new patients. This involves mapping the Retell function call to the NexHealth `POST /patients` API endpoint.

## User Review Required

> [!NOTE]
> The `create_patient` function requires strict parameter handling, especially for `date_of_birth` and `email` formats, as per the OpenAPI spec.

## Proposed Changes

### Retell Handlers

#### [MODIFY] [handlers.py](file:///Users/zulkaif/Development/nex_health/src/app/retell/handlers.py)
- Implement `create_patient` function decorated with `@register_function("create_patient")`.
- It will accept:
    - `first_name`, `last_name` (required)
    - `email` (required, needs validation/format check if strict)
    - `date_of_birth` (required, YYYY-MM-DD)
    - `phone_number` (required)
    - `location_id` (required, integer)
    - `subdomain` (required, string)
    - `provider_id` (required, integer) - OpenAPI default example shows provider_id is needed for intake.
- It will construct the payload:
  ```json
  {
    "provider": { "provider_id": ... },
    "patient": {
      "first_name": ...,
      "last_name": ...,
      "email": ...,
      "bio": {
        "date_of_birth": ...,
        "phone_number": ...,
        "gender": "Female" // Default or optional? Spec says defaults to Female if not provided.
      }
    }
  }
  ```
- It will return success/failure message and patient details.

### Documentation

#### [MODIFY] [RETELL_SCHEMAS.md](file:///Users/zulkaif/Development/nex_health/docs/RETELL_SCHEMAS.md)
- Add the `Create Patient` schema section matching the function signature.

## Verification Plan

### Automated Tests
- Create a new test file `tests/integration/routes/test_retell_handlers.py` to test the handlers directly.
- **Command**: `pytest tests/integration/routes/test_retell_handlers.py`
- Test cases:
    - Successful creation with valid data.
    - Failure with missing required fields (e.g., location_id, subdomain).
