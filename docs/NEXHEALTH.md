## NexHealth Synchronizer API notes

### Authentication
- POST `/authenticates` with your API key in the `Authorization` header.
- Response contains a bearer token valid for 1 hour.
- All other calls require `Authorization: Bearer <token>`.

Required headers:
- `Accept: application/vnd.Nexhealth+json;version=2`
- `Nex-Api-Version: v2`

### Common patterns
Include parameter example:
- `include[]=procedures` on `GET /appointments` to include procedures.

Filtering example:
- `patient_id=...` on `GET /payments` to list payments for a patient.

### Response format
All responses include:
- `code` (boolean)
- `data` (array or object)
- `description` (array)
- `count` (total results for paginated endpoints)
- `error` (array)

### Errors and rate limits
- 401 indicates auth problems.
- 429 indicates rate limiting. Apply backoff and retry.

### Date format
Datetime values are UTC with `YYYY-MM-DDTHH:mm:ssZ`.
