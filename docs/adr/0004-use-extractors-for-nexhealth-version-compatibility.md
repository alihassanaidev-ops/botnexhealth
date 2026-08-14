# Use extractors for NexHealth version compatibility

NexHealth v2 and v3 payload compatibility will be represented by focused mapper and extractor functions with tests, not full upstream Pydantic models or ad hoc parsing inside adapter methods. NexHealth payloads vary by endpoint, PMS, and API version; narrow extractors make the compatibility rules auditable without pretending the upstream schema is tighter than it is.
