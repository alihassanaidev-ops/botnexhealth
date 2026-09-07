"""The access log must not carry query strings.

Patient search sends the name, date of birth, phone and email as query
parameters. Gunicorn's default access log format is ``%(r)s`` — the whole
request line, query string included — so leaving the format unset writes those
values to stdout on every ordinary search, and from there to CloudWatch.

This asserts the format is set and excludes the query, so the protection cannot
be dropped by an edit that looks harmless.
"""

from __future__ import annotations

from src.app import gunicorn_conf


def test_access_log_format_is_overridden() -> None:
    fmt = getattr(gunicorn_conf, "access_log_format", None)
    assert fmt, (
        "access_log_format is unset, so gunicorn falls back to its default "
        "'%(r)s' full request line — which includes the query string"
    )


def test_access_log_never_records_the_query_string() -> None:
    fmt = gunicorn_conf.access_log_format
    # %q is the query string; %(r)s and %(U)s-with-query are the full request
    # line. %U alone is the path, which is what we want.
    assert "%q" not in fmt, "%q writes the query string verbatim"
    assert "%(r)s" not in fmt, "%(r)s is the full request line, query included"
    assert "%U" in fmt, "the path is still needed for the log to be useful"


def test_access_log_still_identifies_the_request() -> None:
    """Scrubbing must not make the log useless for tracing an incident."""
    fmt = gunicorn_conf.access_log_format
    assert "X-Request-ID" in fmt
    assert "%s" in fmt  # status code
