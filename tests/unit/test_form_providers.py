"""Reading what each provider actually sends, and refusing what it did not.

Both webhooks create contacts in a clinic's records from an unauthenticated
request, so the signature check is the only thing standing in front of them. The
parsing tests cover the details that silently lose answers: Typeform's base64
signature, its answers being keyed by field id while mappings are keyed by ref,
and Meta batching several leads into one delivery.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from src.app.api.routes.form_webhooks import _meta_leads
from src.app.services.forms.providers import meta as meta_provider
from src.app.services.forms.providers import typeform as typeform_provider


# ── Typeform ────────────────────────────────────────────────────────────
def _typeform_body() -> dict:
    return {
        "event_id": "01H",
        "form_response": {
            "form_id": "AbC123",
            "token": "response-token-1",
            "submitted_at": "2026-09-01T10:30:00Z",
            "definition": {
                "id": "AbC123",
                "title": "New Patient Enquiry",
                "fields": [
                    {"id": "fid-name", "ref": "name", "type": "short_text"},
                    {"id": "fid-email", "ref": "email", "type": "email"},
                    {"id": "fid-problem", "ref": "problem", "type": "multiple_choice"},
                    {"id": "fid-visit", "ref": "visited", "type": "yes_no"},
                    {"id": "fid-file", "ref": "xray", "type": "file_upload"},
                ],
            },
            "answers": [
                {"type": "text", "text": "Mary Anne Smith", "field": {"id": "fid-name", "ref": "name"}},
                {"type": "email", "email": "mary@example.com", "field": {"id": "fid-email"}},
                {
                    "type": "choice",
                    "choice": {"label": "Toothache"},
                    "field": {"id": "fid-problem", "ref": "problem"},
                },
                {"type": "boolean", "boolean": False, "field": {"id": "fid-visit", "ref": "visited"}},
                {
                    "type": "file_url",
                    "file_url": "https://api.typeform.com/responses/files/secret",
                    "field": {"id": "fid-file", "ref": "xray"},
                },
            ],
            "hidden": {"utm_source": "google"},
        },
    }


def test_typeform_answers_are_keyed_by_ref_even_when_the_answer_omits_it() -> None:
    """Mappings are stored against the ref, so an id-only answer must resolve
    through the delivery's own definition block or the answer is lost."""
    result = typeform_provider.normalize_submission(_typeform_body())
    assert result.answers["email"] == "mary@example.com"
    assert result.answers["name"] == "Mary Anne Smith"


def test_typeform_choice_and_boolean_answers_use_their_declared_type() -> None:
    result = typeform_provider.normalize_submission(_typeform_body())
    assert result.answers["problem"] == "Toothache"
    assert result.answers["visited"] is False


def test_typeform_hidden_fields_arrive_as_answers() -> None:
    """UTM and campaign context is exactly what a practice branches on."""
    result = typeform_provider.normalize_submission(_typeform_body())
    assert result.answers["utm_source"] == "google"


def test_typeform_file_urls_are_dropped() -> None:
    """The URL is a credential in its own right, not an answer to qualify on."""
    result = typeform_provider.normalize_submission(_typeform_body())
    assert "xray" not in result.answers


def test_typeform_carries_the_response_token_for_idempotency() -> None:
    result = typeform_provider.normalize_submission(_typeform_body())
    assert result.external_submission_id == "response-token-1"
    assert result.submitted_at is not None


def test_typeform_signature_is_base64_not_hex() -> None:
    body = json.dumps(_typeform_body()).encode()
    secret = "shhh"
    digest = hmac.new(secret.encode(), body, hashlib.sha256)
    good = "sha256=" + base64.b64encode(digest.digest()).decode()
    hexed = "sha256=" + digest.hexdigest()

    assert typeform_provider.verify_webhook_signature(body, good, secret)
    assert not typeform_provider.verify_webhook_signature(body, hexed, secret)


def test_typeform_signature_fails_closed_without_a_secret() -> None:
    body = b"{}"
    assert not typeform_provider.verify_webhook_signature(body, "sha256=x", "")
    assert not typeform_provider.verify_webhook_signature(body, None, "shhh")


# ── Meta ────────────────────────────────────────────────────────────────
def test_meta_batches_several_leads_into_one_delivery() -> None:
    payload = {
        "object": "page",
        "entry": [
            {
                "id": "page-1",
                "changes": [
                    {"field": "leadgen", "value": {"leadgen_id": "lead-1", "form_id": "f-1"}},
                    {"field": "leadgen", "value": {"leadgen_id": "lead-2", "form_id": "f-1"}},
                    {"field": "feed", "value": {"post_id": "p-1"}},
                ],
            },
            {
                "id": "page-2",
                "changes": [
                    {"field": "leadgen", "value": {"leadgen_id": "lead-3"}},
                ],
            },
        ],
    }
    assert _meta_leads(payload) == [
        ("page-1", "lead-1", "f-1"),
        ("page-1", "lead-2", "f-1"),
        ("page-2", "lead-3", None),
    ]


def test_meta_ignores_anything_that_is_not_a_page_leadgen_change() -> None:
    assert _meta_leads({"object": "instagram", "entry": [{"id": "x"}]}) == []
    assert _meta_leads({}) == []


def test_meta_signature_fails_closed_without_an_app_secret(monkeypatch) -> None:
    """An unverifiable delivery is indistinguishable from a forged one, and
    this endpoint writes people into a clinic's records."""
    monkeypatch.setattr(meta_provider.settings, "meta_app_secret", None)
    assert not meta_provider.verify_webhook_signature(b"{}", "sha256=anything")


def test_meta_signature_is_hex_over_the_exact_bytes(monkeypatch) -> None:
    monkeypatch.setattr(meta_provider.settings, "meta_app_secret", "app-secret")
    body = b'{"object":"page"}'
    expected = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()

    assert meta_provider.verify_webhook_signature(body, f"sha256={expected}")
    assert meta_provider.verify_webhook_signature(body, expected)
    # Re-serialising the body before checking would break this.
    assert not meta_provider.verify_webhook_signature(b'{"object": "page"}', expected)
