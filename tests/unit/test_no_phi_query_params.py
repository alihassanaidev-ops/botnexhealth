"""No route may accept a PHI-bearing identifier as a URL query parameter.

HIPAA audit H-1: patient name, date of birth, phone and email were read from
the query string, so they were written to the gunicorn access log, browser
history and any proxy log — stores outside the platform's PHI controls. The
access log no longer records the query string, but the identifiers still
belong in the request body, not the URL. This fails when a new endpoint (or a
regression) puts one back.
"""

from __future__ import annotations

from src.app.main import app

#: Parameter names that carry a §164.514(b)(2) identifier.
_PHI_PARAM_NAMES = {
    "q",
    "email",
    "phone_number",
    "date_of_birth",
    "search",
    "full_name",
}


def _phi_query_params() -> list[str]:
    offenders: list[str] = []
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        path = getattr(route, "path", "?")
        for param in dependant.query_params:
            if param.name in _PHI_PARAM_NAMES:
                offenders.append(f"{path} [{','.join(route.methods)}] -> {param.name}")
    return offenders


def test_no_route_reads_a_patient_identifier_from_the_query_string():
    offenders = _phi_query_params()
    assert not offenders, (
        "These routes still accept a PHI identifier as a query parameter; move "
        "it into the request body:\n  " + "\n  ".join(sorted(offenders))
    )
