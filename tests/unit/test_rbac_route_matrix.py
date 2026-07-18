"""Route-level RBAC boundary tests.

These tests intentionally sit above the dependency unit tests: every FastAPI
route must be classified here so a new endpoint cannot accidentally ship
without an explicit access-control boundary.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from src.app.api import deps as auth_deps
from src.app.main import app
from src.app.models.user import User, UserRole


PUBLIC = "public"
SIGNED_WEBHOOK = "signed_webhook"
TICKET_AUTH = "ticket_auth"
MFA_TICKET = "mfa_ticket"

SUPER_ADMIN = "get_current_admin"
SUPER_ADMIN_STRICT = "get_current_super_admin"
INSTITUTION_ADMIN = "get_current_institution_admin"
INSTITUTION_USER = "get_current_institution_user"
LOCATION_ADMIN = "get_current_location_admin"
INSTITUTION_OR_LOCATION_ADMIN = "get_current_institution_or_location_admin"
LOCATION_STAFF_OR_ADMIN = "get_current_location_staff_or_admin"
INSTITUTION_OR_LOCATION_USER = "get_current_institution_or_location_user"
GROUP = "get_current_group_admin"
ACTIVE_USER = "get_current_active_user"


ROUTES_BY_BOUNDARY: dict[str, tuple[str, ...]] = {
    PUBLIC: (
        "GET /livez",
        "GET /readyz",
        "POST /api/auth/login",
        "POST /api/auth/token",
        "POST /api/auth/forgot-password",
        "POST /api/auth/reset-password",
        "POST /api/auth/set-password",
        "POST /api/auth/refresh",
        "POST /api/auth/logout",
        "GET /api/email/unsubscribe",
    ),
    MFA_TICKET: (
        "POST /api/auth/mfa/webauthn/register/options",
        "POST /api/auth/mfa/webauthn/register/verify",
        "POST /api/auth/mfa/webauthn/authenticate/options",
        "POST /api/auth/mfa/webauthn/authenticate/verify",
        "POST /api/auth/mfa/totp/setup/options",
        "POST /api/auth/mfa/totp/setup/verify",
        "POST /api/auth/mfa/totp/verify",
        "POST /api/auth/mfa/recovery-code/verify",
    ),
    SIGNED_WEBHOOK: (
        "POST /api/v1/retell/functions",
        "POST /api/v1/retell/webhook",
        "POST /api/v1/twilio/webhooks/inbound-sms",
        "POST /api/v1/twilio/webhooks/sms-status",
        "POST /api/v1/nexhealth/webhooks/appointments",
        "POST /api/email/webhooks/resend",
    ),
    TICKET_AUTH: (
        "GET /api/institution/events",
    ),
    ACTIVE_USER: (
        "GET /api/v1/health",
        "GET /api/auth/users/me",
        "GET /api/auth/mfa/status",
        "POST /api/auth/mfa/recovery-codes/regenerate",
        "GET /api/auth/mfa/webauthn",
        "DELETE /api/auth/mfa/webauthn/{credential_pk}",
        "POST /api/auth/mfa/totp/disable",
        # Step-up flow — gated by an authenticated session plus an
        # elevated MFA ticket in the body. The active-user dependency
        # is the outer boundary; the inner step-up consume is checked
        # by the dedicated tests in test_mfa_step_up.py.
        "POST /api/auth/mfa/step-up/challenge",
        "POST /api/auth/mfa/step-up/totp/verify",
        "POST /api/auth/mfa/step-up/webauthn/authenticate/options",
        "POST /api/auth/mfa/step-up/webauthn/authenticate/verify",
        "POST /api/auth/mfa/step-up/recovery-code/verify",
        # Add-factor flow (Security settings): step-up gates the
        # /options endpoints; the /verify endpoints consume the
        # enrollment ticket the /options endpoint returned. RBAC outer
        # boundary is "must be authenticated"; the step-up validation
        # happens inside the handler.
        "POST /api/auth/mfa/factors/webauthn/register/options",
        "POST /api/auth/mfa/factors/webauthn/register/verify",
        "POST /api/auth/mfa/factors/totp/setup/options",
        "POST /api/auth/mfa/factors/totp/setup/verify",
        "GET /api/institution/setup/overview",
        "GET /api/institution/setup/locations",
        "GET /api/institution/setup/providers",
        "GET /api/institution/setup/appointment-types",
        "GET /api/institution/setup/operatories",
        "GET /api/institution/setup/descriptors",
        "GET /api/institution/setup/availabilities",
        "GET /api/institution/setup/operating-hours",
        "GET /api/institution/setup/breaks",
        "GET /api/institution/calls",
        "GET /api/institution/calls/{call_id}",
        "POST /api/institution/calls/{call_id}/reveal/transcript",
        "POST /api/institution/calls/{call_id}/reveal/recording",
        "POST /api/institution/calls/{call_id}/reveal/custom-fields/{field_key}",
        "POST /api/institution/calls/{call_id}/reveal/phone",
        "PATCH /api/institution/calls/{call_id}/resolve",
        "PATCH /api/institution/calls/{call_id}/status",
        "GET /api/institution/statuses",
        "GET /api/institution/contacts",
        "GET /api/institution/contacts/{contact_id}",
        "POST /api/institution/contacts/{contact_id}/reveal/phone",
        "GET /api/institution/dashboard/summary",
        "GET /api/institution/dashboard/monthly-metrics",
        "GET /api/institution/usage/summary",
        "GET /api/institution/usage/by-campaign",
        "GET /api/institution/custom-fields/definitions",
        "GET /api/institution/notifications",
        "GET /api/institution/notifications/unread-count",
        "PATCH /api/institution/notifications/{notification_id}/read",
        "POST /api/institution/notifications/mark-all-read",
        "GET /api/institution/callbacks",
        "GET /api/institution/notification-preferences",
        "PUT /api/institution/notification-preferences",
        "POST /api/institution/events/ticket",
    ),
    SUPER_ADMIN: (
        "GET /api/v1/nexhealth/institutions",
        "GET /api/v1/nexhealth/institutions/{institution_id}",
        "GET /api/v1/nexhealth/locations",
        "GET /api/v1/nexhealth/locations/{location_id}",
        "GET /api/v1/nexhealth/locations/{location_id}/appointment_descriptors",
        "POST /api/auth/admin/users/{user_id}/unlock",
        "GET /api/admin/institutions/retell/agents",
        "GET /api/admin/institutions/retell/agents/{agent_id}",
        "GET /api/admin/institutions/nexhealth/locations",
        "GET /api/admin/institutions/audit-logs",
        "GET /api/admin/institutions",
        "POST /api/admin/institutions",
        "GET /api/admin/institution-groups",
        "POST /api/admin/institution-groups",
        "POST /api/admin/institution-groups/{slug}/institutions/{inst_slug}",
        "DELETE /api/admin/institution-groups/{slug}/institutions/{inst_slug}",
        "GET /api/admin/institutions/{slug}",
        "PATCH /api/admin/institutions/{slug}",
        "DELETE /api/admin/institutions/{slug}",
        "POST /api/admin/institutions/{slug}/reinvite",
        "POST /api/admin/institutions/{slug}/test-call-notification",
        "POST /api/admin/institutions/{slug}/locations",
        "GET /api/admin/institutions/{slug}/locations",
        "GET /api/admin/institutions/{slug}/locations/{loc_slug}",
        "PATCH /api/admin/institutions/{slug}/locations/{loc_slug}",
        "DELETE /api/admin/institutions/{slug}/locations/{loc_slug}",
        "POST /api/admin/institutions/{slug}/locations/{loc_slug}/sync",
        "POST /api/admin/institutions/{slug}/locations/{loc_slug}/invite",
        "GET /api/admin/institutions/{slug}/locations/{loc_slug}/users",
        "POST /api/admin/institutions/{slug}/locations/{loc_slug}/reinvite",
        "GET /api/admin/institutions/{slug}/locations/{loc_slug}/operating-hours",
        "PUT /api/admin/institutions/{slug}/locations/{loc_slug}/operating-hours",
        "GET /api/admin/institutions/{slug}/locations/{loc_slug}/breaks",
        "POST /api/admin/institutions/{slug}/locations/{loc_slug}/breaks",
        "DELETE /api/admin/institutions/{slug}/locations/{loc_slug}/breaks/{break_id}",
        "GET /api/admin/institutions/{slug}/provisioning",
        "PATCH /api/admin/institutions/{slug}/provisioning",
        "DELETE /api/admin/institutions/{slug}/provisioning/twilio",
        "GET /api/admin/users",
        "DELETE /api/admin/users/{user_id}",
        "POST /api/admin/users/{user_id}/reinvite",
        "GET /api/admin/twilio/phone-numbers",
        "POST /api/admin/twilio/send-sms",
        "GET /api/admin/sms/locations",
        "GET /api/admin/sms/logs",
        "GET /api/admin/sms/suppressions",
        "POST /api/admin/sms/suppressions",
        "POST /api/admin/sms/suppressions/{suppression_id}/release",
        "GET /api/admin/dead-letter-events",
        "POST /api/admin/dead-letter-events/{event_id}/discard",
        "POST /api/admin/dead-letter-events/{event_id}/replay",
    ),
    INSTITUTION_ADMIN: (
        "POST /api/institution/users/invite-institution-admin",
        "GET /api/institution/users",
        "POST /api/institution/users/invite",
        "POST /api/institution/users/{user_id}/deactivate",
        "POST /api/institution/users/{user_id}/reinvite",
        "POST /api/institution/locations/{loc_slug}/invite-location-admin",
        "GET /api/institution/billing-email",
        "PUT /api/institution/billing-email",
        "GET /api/institution/roi/config",
        "PUT /api/institution/roi/config",
        "GET /api/institution/roi/calculate",
        "GET /api/institution/audit-logs",
        "GET /api/institution/dashboard/aggregate",
        "POST /api/institution/do-not-contact",
        "DELETE /api/institution/do-not-contact",
        "GET /api/institution/do-not-contact",
        "POST /api/institution/custom-fields/definitions",
        "PATCH /api/institution/custom-fields/definitions/{definition_id}",
        "DELETE /api/institution/custom-fields/definitions/{definition_id}",
        "GET /api/institution/email-templates",
        "POST /api/institution/email-templates/preview/live",
        "POST /api/institution/email-templates/validate",
        "GET /api/institution/email-templates/{template_type}",
        "PUT /api/institution/email-templates/{template_type}",
        "POST /api/institution/email-templates/{template_type}/reset",
        "GET /api/institution/email-templates/{template_type}/preview",
        "GET /api/institution/email-templates/{template_type}/variables",
        "GET /api/institution/notification-recipients",
        "POST /api/institution/notification-recipients",
        "PUT /api/institution/notification-recipients/{recipient_id}",
        "DELETE /api/institution/notification-recipients/{recipient_id}",
    ),
    INSTITUTION_USER: (
        "POST /api/automation/workflows",
        "GET /api/automation/workflows",
        "PATCH /api/automation/workflows/{workflow_id}",
        "POST /api/automation/workflows/{workflow_id}/publish",
        "POST /api/automation/workflows/{workflow_id}/pause",
        "POST /api/automation/workflows/{workflow_id}/resume",
        "POST /api/automation/workflows/{workflow_id}/archive",
        "POST /api/automation/templates/{template_id}/instantiate",
        "POST /api/automation/workflows/{workflow_id}/bulk-enroll",
        "POST /api/automation/workflows/validate",
        "POST /api/automation/workflows/dry-run",
        "GET /api/automation/workflows/channel-readiness",
        "POST /api/automation/workflows/{workflow_id}/launch-checklist/preview",
        "PUT /api/automation/workflows/{workflow_id}/audience",
        "POST /api/automation/workflows/{workflow_id}/audience/enroll",
        "POST /api/automation/workflows/{workflow_id}/emergency-halt",
        "GET /api/automation/workflows/outbound-halt",
        "POST /api/automation/workflows/outbound-halt",
        "DELETE /api/automation/workflows/outbound-halt",
    ),
    LOCATION_ADMIN: (
        "GET /api/institution/location/users",
        "POST /api/institution/location/users/{user_id}/deactivate",
        "POST /api/institution/locations/{loc_slug}/invite-staff",
        "GET /api/institution/location/audit-logs",
    ),
    INSTITUTION_OR_LOCATION_ADMIN: (
        "GET /api/automation/workflows/merge-fields",
        "GET /api/automation/workflows/{workflow_id}/launch-checklist",
        "GET /api/automation/workflows/{workflow_id}/overview",
        "GET /api/automation/workflows/{workflow_id}/versions",
        "GET /api/automation/workflows/{workflow_id}/analytics",
        "GET /api/automation/workflows/{workflow_id}/operations",
        "GET /api/automation/workflows/{workflow_id}/audience",
        "POST /api/automation/workflows/{workflow_id}/audience/preview",
        "GET /api/automation/workflows/{workflow_id}/runs/{run_id}/timeline",
        "GET /api/automation/campaign-analytics",
        "POST /api/v1/pms/appointment-types",
        "POST /api/v1/pms/setup/availabilities",
        "PATCH /api/v1/pms/setup/availabilities/{availability_id}",
        "POST /api/institution/locations/{loc_slug}/transfer-numbers",
        "PATCH /api/institution/locations/{loc_slug}/transfer-numbers/{transfer_id}",
        "DELETE /api/institution/locations/{loc_slug}/transfer-numbers/{transfer_id}",
        "PATCH /api/institution/setup/providers/{provider_id}",
        "POST /api/institution/setup/appointment-types",
        "PATCH /api/institution/setup/appointment-types/{source_id}",
        "DELETE /api/institution/setup/appointment-types/{source_id}",
        "POST /api/institution/setup/availabilities",
        "PATCH /api/institution/setup/availabilities/{source_id}",
        "POST /api/institution/setup/sync",
        "PUT /api/institution/setup/operating-hours",
        "POST /api/institution/setup/breaks",
        "DELETE /api/institution/setup/breaks/{break_id}",
        "POST /api/institution/locations/{loc_slug}/insurance-plans",
        "PATCH /api/institution/locations/{loc_slug}/insurance-plans/{plan_id}",
        "DELETE /api/institution/locations/{loc_slug}/insurance-plans/{plan_id}",
        "POST /api/institution/contacts/{contact_id}/merge",
        "POST /api/institution/contacts/{contact_id}/unmerge",
        "POST /api/institution/statuses",
        "PATCH /api/institution/statuses/{status_id}",
        "DELETE /api/institution/statuses/{status_id}",
        "GET /api/automation/workflows/{workflow_id}",
        "POST /api/automation/workflows/{workflow_id}/enroll",
        "GET /api/automation/workflows/{workflow_id}/runs",
        "GET /api/automation/workflows/{workflow_id}/runs/{run_id}",
        "POST /api/automation/workflows/{workflow_id}/runs/{run_id}/cancel",
        "GET /api/automation/templates",
        "GET /api/automation/templates/{template_id}",
        "POST /api/outbound-voice/profiles",
        "GET /api/outbound-voice/profiles",
        "GET /api/outbound-voice/profiles/{profile_id}",
        "PATCH /api/outbound-voice/profiles/{profile_id}",
        "DELETE /api/outbound-voice/profiles/{profile_id}",
    ),
    INSTITUTION_OR_LOCATION_USER: (
        "GET /api/outbound-voice/attempts",
        "GET /api/v1/pms/patients",
        "POST /api/v1/pms/patients",
        "GET /api/v1/pms/slots",
        "POST /api/v1/pms/appointments",
        "PATCH /api/v1/pms/appointments/{appointment_id}/cancel",
        "POST /api/v1/pms/appointments/{old_appointment_id}/reschedule",
        "GET /api/v1/pms/appointment-types",
        "GET /api/v1/pms/providers",
        "GET /api/v1/pms/operatories",
        "GET /api/v1/pms/locations",
        "GET /api/v1/pms/locations/{location_id}",
        "GET /api/v1/pms/setup/capabilities",
        "GET /api/v1/pms/setup/steps",
        "GET /api/v1/pms/setup/descriptors",
        "GET /api/v1/pms/setup/availabilities",
        "GET /api/institution/me",
        "GET /api/institution/locations",
        "GET /api/institution/locations/{loc_slug}/operating-hours",
        "PUT /api/institution/locations/{loc_slug}/operating-hours",
        "PATCH /api/institution/locations/{loc_slug}/timezone",
        "GET /api/institution/transfer-numbers",
        "GET /api/institution/locations/{loc_slug}/insurance-plans",
        "GET /api/institution/sms/logs",
        "GET /api/institution/sms/logs/{sms_id}",
        "POST /api/institution/sms/logs/{sms_id}/reveal-phone",
        "POST /api/institution/sms/logs/{sms_id}/reveal-body",
    ),
    GROUP: (
        "GET /api/group/me",
        "GET /api/group/dashboard",
        "GET /api/group/institution/{institution_id}/dashboard",
        "GET /api/group/usage-summary",
    ),
    SUPER_ADMIN_STRICT: (
        # Break-glass MFA reset — strictly SUPER_ADMIN, not the broader
        # ``get_current_admin`` boundary used elsewhere. Resetting another
        # user's MFA is the rare operation where the
        # institution-admin-as-acceptable-admin shortcut does not apply.
        "POST /api/auth/admin/users/{user_id}/mfa/reset",
    ),
}


AUTH_BOUNDARIES = {
    SUPER_ADMIN,
    SUPER_ADMIN_STRICT,
    INSTITUTION_ADMIN,
    INSTITUTION_USER,
    LOCATION_ADMIN,
    INSTITUTION_OR_LOCATION_ADMIN,
    LOCATION_STAFF_OR_ADMIN,
    INSTITUTION_OR_LOCATION_USER,
    GROUP,
    ACTIVE_USER,
}

BOUNDARY_PRECEDENCE = (
    SUPER_ADMIN,
    SUPER_ADMIN_STRICT,
    INSTITUTION_ADMIN,
    INSTITUTION_USER,
    LOCATION_ADMIN,
    INSTITUTION_OR_LOCATION_ADMIN,
    LOCATION_STAFF_OR_ADMIN,
    INSTITUTION_OR_LOCATION_USER,
    GROUP,
    ACTIVE_USER,
)

ALLOWED_ROLES_BY_BOUNDARY: dict[str, set[UserRole]] = {
    SUPER_ADMIN: {UserRole.SUPER_ADMIN},
    SUPER_ADMIN_STRICT: {UserRole.SUPER_ADMIN},
    INSTITUTION_ADMIN: {UserRole.INSTITUTION_ADMIN},
    INSTITUTION_USER: {UserRole.INSTITUTION_ADMIN},
    LOCATION_ADMIN: {UserRole.LOCATION_ADMIN},
    INSTITUTION_OR_LOCATION_ADMIN: {
        UserRole.INSTITUTION_ADMIN,
        UserRole.LOCATION_ADMIN,
    },
    LOCATION_STAFF_OR_ADMIN: {UserRole.LOCATION_ADMIN, UserRole.STAFF},
    INSTITUTION_OR_LOCATION_USER: {
        UserRole.INSTITUTION_ADMIN,
        UserRole.LOCATION_ADMIN,
        UserRole.STAFF,
    },
    GROUP: {UserRole.GROUP_ADMIN},
    ACTIVE_USER: {
        UserRole.SUPER_ADMIN,
        UserRole.INSTITUTION_ADMIN,
        UserRole.LOCATION_ADMIN,
        UserRole.STAFF,
        # A GROUP_ADMIN is an active user, so it passes the outer active-user
        # gate. Institution data handlers still hard-reject it (no institution_id
        # → 400) and RLS returns no tenant rows, so no PHI is reachable.
        UserRole.GROUP_ADMIN,
    },
}

EXPECTED_ROUTE_BOUNDARIES = {
    route: boundary
    for boundary, routes in ROUTES_BY_BOUNDARY.items()
    for route in routes
}


def _routes() -> dict[str, APIRoute]:
    routes: dict[str, APIRoute] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or ()):
            if method == "HEAD":
                continue
            routes[f"{method} {route.path}"] = route
    return routes


def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()

    def walk(dependant) -> None:
        for dependency in dependant.dependencies:
            call = dependency.call
            name = getattr(call, "__name__", "")
            if name:
                names.add(name)
            walk(dependency)

    walk(route.dependant)
    return names


def _selected_auth_boundary(route: APIRoute) -> str | None:
    names = _dependency_names(route)
    for boundary in BOUNDARY_PRECEDENCE:
        if boundary in names:
            return boundary
    return None


def _user(
    role: UserRole,
    *,
    location_id: str | None = "22222222-2222-2222-2222-222222222222",
    is_active: bool = True,
) -> User:
    return User(
        id="11111111-1111-1111-1111-111111111111",
        email=f"{role.value.lower()}@example.com",
        role=role.value,
        institution_id=None
        if role in (UserRole.SUPER_ADMIN, UserRole.GROUP_ADMIN)
        else "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        location_id=location_id
        if role in (UserRole.LOCATION_ADMIN, UserRole.STAFF)
        else None,
        group_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        if role == UserRole.GROUP_ADMIN
        else None,
        is_active=is_active,
    )


def _auth_dependency(boundary: str) -> Callable:
    return getattr(auth_deps, boundary)


def test_route_matrix_has_no_duplicate_expectations():
    expected_routes = [
        route
        for routes in ROUTES_BY_BOUNDARY.values()
        for route in routes
    ]
    duplicates = sorted(route for route, count in Counter(expected_routes).items() if count > 1)

    assert duplicates == []


def test_every_route_has_an_explicit_rbac_boundary_expectation():
    actual = set(_routes())
    expected = set(EXPECTED_ROUTE_BOUNDARIES)

    assert actual - expected == set()
    assert expected - actual == set()


@pytest.mark.parametrize("route_key", sorted(EXPECTED_ROUTE_BOUNDARIES))
def test_route_uses_expected_auth_boundary(route_key: str):
    route = _routes()[route_key]
    expected_boundary = EXPECTED_ROUTE_BOUNDARIES[route_key]
    auth_boundary = _selected_auth_boundary(route)

    if expected_boundary in AUTH_BOUNDARIES:
        assert auth_boundary == expected_boundary
        assert ACTIVE_USER in _dependency_names(route)
    else:
        assert auth_boundary is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route_key",
    sorted(
        route
        for route, boundary in EXPECTED_ROUTE_BOUNDARIES.items()
        if boundary in AUTH_BOUNDARIES
    ),
)
async def test_endpoint_rbac_role_matrix(route_key: str):
    boundary = EXPECTED_ROUTE_BOUNDARIES[route_key]
    allowed_roles = ALLOWED_ROLES_BY_BOUNDARY[boundary]
    dependency = _auth_dependency(boundary)

    for role in UserRole:
        user = _user(role)
        if role in allowed_roles:
            assert await dependency(user) is user
        else:
            with pytest.raises(HTTPException) as exc:
                await dependency(user)
            assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dependency",
    (
        auth_deps.get_current_location_admin,
        auth_deps.get_current_location_staff_or_admin,
    ),
)
async def test_location_scoped_boundaries_require_location_assignment(dependency: Callable):
    for role in (UserRole.LOCATION_ADMIN, UserRole.STAFF):
        user = _user(role, location_id=None)
        with pytest.raises(HTTPException) as exc:
            await dependency(user)
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_active_user_boundary_rejects_inactive_accounts():
    with pytest.raises(HTTPException) as exc:
        await auth_deps.get_current_active_user(
            _user(UserRole.INSTITUTION_ADMIN, is_active=False)
        )
    assert exc.value.status_code == 403


def test_internal_admin_surfaces_remain_super_admin_only():
    for route_key, boundary in EXPECTED_ROUTE_BOUNDARIES.items():
        _method, path = route_key.split(" ", 1)
        if path.startswith((
            "/api/admin/",
            "/api/auth/admin/",
            "/api/v1/nexhealth/",
        )) and not path.startswith("/api/v1/nexhealth/webhooks/"):
            # Webhook endpoints are externally called with signature verification,
            # not admin surfaces — they use SIGNED_WEBHOOK boundary instead.
            assert boundary in {SUPER_ADMIN, SUPER_ADMIN_STRICT}


def test_user_management_surfaces_keep_admin_boundaries():
    for route_key, boundary in EXPECTED_ROUTE_BOUNDARIES.items():
        _method, path = route_key.split(" ", 1)
        if path.startswith("/api/institution/users"):
            assert boundary == INSTITUTION_ADMIN
        if path.startswith("/api/institution/location/users"):
            assert boundary == LOCATION_ADMIN
        if path.endswith("/invite-staff"):
            assert boundary == LOCATION_ADMIN


def test_staff_cannot_cross_mutation_boundaries_at_dependency_layer():
    mutation_boundaries = {
        INSTITUTION_ADMIN,
        LOCATION_ADMIN,
        INSTITUTION_OR_LOCATION_ADMIN,
    }

    for route_key, boundary in EXPECTED_ROUTE_BOUNDARIES.items():
        if boundary not in mutation_boundaries:
            continue
        assert UserRole.STAFF not in ALLOWED_ROLES_BY_BOUNDARY[boundary], route_key
