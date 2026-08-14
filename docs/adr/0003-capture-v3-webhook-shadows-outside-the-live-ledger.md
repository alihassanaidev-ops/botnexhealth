# Capture v3 webhook shadows outside the live ledger

V3 webhook subscriptions used for validation will call distinct shadow routes and write to a shadow capture path instead of the existing NexHealth webhook event ledger. The live ledger drives idempotency, workflow enrollment, retry accounting, and dead-letter behavior, so using it for comparison traffic would make duplicate/version-overlap behavior harder to reason about.

Shadow capture will store encrypted raw payloads, redacted payloads, hashes, API contract, event/resource metadata, provider delivery/subscription identifiers when present, parse status, parse error summaries, extracted business event identity fields, institution/location resolution results, and retention timestamps. Shadow subscription lifecycle state will be separate from the live per-location subscription row. After validation, real cutover handling will deduplicate by business event identity rather than by provider delivery version.
