## Project documentation

### Scope
This backend powers a HIPAA-minded voice agent that uses NexHealth to read and write scheduling, patient, and financial data for dental and medical practices.

### High-level architecture
- FastAPI service exposes internal endpoints for the voice agent.
- NexHealth client handles authentication, version headers, and retries.
- Tokens are stored in memory only and refreshed automatically.

### Data flow (voice agent)
1. Voice agent calls backend with a task (schedule, reschedule, confirm).
2. Backend validates authorization and required consent.
3. Backend calls NexHealth and returns the minimum data needed.
4. Voice agent responds to the patient and logs outcome metadata.

### HIPAA-minded practices
These are implementation notes, not legal advice.
- Avoid logging PHI. Use request ids and high-level event logs.
- Restrict access to the backend with service-to-service auth.
- Store secrets in environment variables or a secrets manager.
- Encrypt any persisted PHI at rest and in transit.
- Add audit logs for access to patient data.
- Apply least-privilege for NexHealth keys.
- Rate limit inbound requests to protect downstream APIs.

### Rate limits
NexHealth rate limits require backoff on 429 responses. The client should add retry with jitter or a small sleep on 429 and 5xx responses.

### Next steps
- Add authentication for the voice agent (service tokens or mutual TLS).
- Add structured audit logging and PHI-safe log filtering.
- Add background jobs for heavy tasks or batch sync.
