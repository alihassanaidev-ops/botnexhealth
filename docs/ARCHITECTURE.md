# Architecture & Code Quality

This document explains the production-grade architecture, SOLID principles, and DRY patterns used in this codebase.

## SOLID Principles

### Single Responsibility Principle (SRP)

Each class has one clear responsibility:

- **`AuthService`**: Handles authentication only
- **`TokenManager`**: Manages token lifecycle and caching
- **`NexHealthHTTPClient`**: Handles HTTP requests with retry logic
- **`NexHealthClient`**: Orchestrates the above (Facade pattern)
- **`Settings`**: Configuration management

### Open/Closed Principle (OCP)

The code is open for extension, closed for modification:

- **`TokenCache` Protocol**: Allows different cache implementations (in-memory, Redis, etc.)
- **`AuthConfig` Protocol**: Allows different configuration sources
- New HTTP client behaviors can be added via composition

### Liskov Substitution Principle (LSP)

Protocols ensure substitutability:

- Any `TokenCache` implementation can replace `InMemoryTokenCache`
- Any `AuthConfig` implementation can replace `Settings`

### Interface Segregation Principle (ISP)

Protocols are minimal and focused:

- `TokenCache` only defines what's needed for caching
- `AuthConfig` only defines what's needed for authentication

### Dependency Inversion Principle (DIP)

High-level modules depend on abstractions:

- `NexHealthClient` depends on `AuthConfig` protocol, not concrete `Settings`
- `TokenManager` depends on `TokenCache` protocol
- FastAPI routes use dependency injection

## DRY (Don't Repeat Yourself)

### Shared Header Building

Headers are built in one place:

- `AuthService._build_auth_headers()`: Centralized auth headers
- `NexHealthHTTPClient._build_headers()`: Centralized API headers

### Retry Logic

Rate limit handling is centralized in `NexHealthHTTPClient.request()`:

- Automatic retry for 429 responses
- Exponential backoff
- Configurable retry attempts

### Error Handling

Structured exception hierarchy:

- `NexHealthError`: Base exception
- `NexHealthAuthenticationError`: Auth failures
- `NexHealthAPIError`: API errors with error list
- `NexHealthRateLimitError`: Rate limit with retry-after

## Production-Grade Features

### 1. Logging

- Structured logging with appropriate levels
- No PHI in logs (HIPAA compliance)
- Request/response logging at appropriate levels

### 2. Error Handling

- Specific exception types for different error scenarios
- Proper HTTP status code mapping
- Error messages without exposing internals

### 3. Rate Limit Handling

- Automatic retry with exponential backoff
- Respects `Retry-After` header
- Configurable max retries

### 4. Connection Management

- HTTP connection pooling
- Proper resource cleanup (context managers)
- Configurable timeouts

### 5. Dependency Injection

- FastAPI dependency injection for testability
- Protocol-based abstractions
- Easy to mock in tests

### 6. Type Safety

- Full type hints
- Protocol-based interfaces
- Pydantic settings validation

## Architecture Patterns

### Facade Pattern

`NexHealthClient` provides a simple interface that hides the complexity of:
- Authentication
- Token management
- HTTP retry logic
- Error handling

### Strategy Pattern

`TokenCache` protocol allows different caching strategies:
- In-memory (single process)
- Redis (multi-process/worker)
- Database-backed

### Dependency Injection

FastAPI's dependency system provides:
- Testability (easy to mock)
- Configuration management
- Lifecycle management

## Testing Considerations

The architecture supports testing:

1. **Unit Tests**: Mock protocols and dependencies
2. **Integration Tests**: Use test configuration
3. **E2E Tests**: Use real NexHealth sandbox

Example test structure:
```python
# Mock AuthConfig
class TestConfig:
    api_key = "test-key"
    base_url = "https://test.nexhealth.info"
    # ...

# Mock TokenCache
class TestTokenCache:
    async def get(self) -> str | None: ...
    async def set(self, token: str, expires_in: int) -> None: ...
```

## Future Enhancements

1. **Redis Token Cache**: For multi-worker deployments
2. **Circuit Breaker**: For resilience
3. **Metrics/Monitoring**: Prometheus/StatsD integration
4. **Request ID Tracking**: For distributed tracing
5. **Request/Response Validation**: Pydantic models for API responses
