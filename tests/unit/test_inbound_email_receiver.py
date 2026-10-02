"""Unit tests for inbound receipt notifications.

The notification is metadata only — the provider caps it well below a message's
maximum size — so this layer's job is to say where the body is and what the
provider thought of it. A malformed notification must never stall the queue
behind it.
"""

from __future__ import annotations

import json

import pytest

from src.app.services.email.inbound_receiver import (
    InboundMailStore,
    parse_notification,
    storage_key_for,
)


def _ses_notification(**overrides) -> dict:
    receipt = {
        "recipients": ["r+abc.def.ghi.jkl.0123456789ab@inbound.example.com"],
        "spamVerdict": {"status": "PASS"},
        "virusVerdict": {"status": "PASS"},
        "spfVerdict": {"status": "PASS"},
        "dkimVerdict": {"status": "PASS"},
        "dmarcVerdict": {"status": "PASS"},
        "action": {"type": "S3", "objectKey": "inbound/abc123"},
    }
    receipt.update(overrides.pop("receipt", {}))
    body = {
        "mail": {"messageId": "abc123", "destination": receipt["recipients"]},
        "receipt": receipt,
    }
    body.update(overrides)
    return body


def _sns_envelope(inner: dict) -> str:
    """Queue messages arrive wrapped by SNS: JSON inside a JSON string."""
    return json.dumps({"Type": "Notification", "Message": json.dumps(inner)})


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parses_a_bare_ses_notification():
    parsed = parse_notification(json.dumps(_ses_notification()))
    assert parsed is not None
    assert parsed.message_id == "abc123"
    assert parsed.storage_key == "inbound/abc123"
    assert parsed.verdicts.spam == "PASS"


def test_unwraps_the_sns_envelope():
    parsed = parse_notification(_sns_envelope(_ses_notification()))
    assert parsed is not None
    assert parsed.message_id == "abc123"


def test_accepts_a_dict_as_well_as_a_string():
    parsed = parse_notification(_ses_notification())
    assert parsed is not None


def test_recipients_are_lowercased():
    notification = _ses_notification(
        receipt={"recipients": ["R+ABC@Inbound.Example.COM"]}
    )
    parsed = parse_notification(json.dumps(notification))
    assert parsed.recipients == ["r+abc@inbound.example.com"]


def test_failing_verdicts_are_carried_through():
    notification = _ses_notification(
        receipt={"spamVerdict": {"status": "FAIL"}, "virusVerdict": {"status": "FAIL"}}
    )
    parsed = parse_notification(json.dumps(notification))
    assert parsed.verdicts.is_hostile is True


def test_missing_verdicts_are_not_hostile():
    """Absent is not the same as failing — a missing verdict must not quarantine
    a legitimate reply."""
    notification = {"mail": {"messageId": "x"}, "receipt": {"recipients": []}}
    parsed = parse_notification(json.dumps(notification))
    assert parsed.verdicts.is_hostile is False


# ---------------------------------------------------------------------------
# Malformed input — must return None, never raise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "not json",
        "{}",
        json.dumps({"Type": "Notification", "Message": "not json"}),
        json.dumps({"unrelated": "payload"}),
        json.dumps({"Type": "SubscriptionConfirmation", "Message": "{}"}),
    ],
)
def test_unrecognised_payloads_return_none(payload):
    """A malformed notification is discarded, not retried — retrying will not
    make it parse, and it must not block the queue behind it."""
    assert parse_notification(payload) is None


# ---------------------------------------------------------------------------
# Storage key
# ---------------------------------------------------------------------------


def test_storage_key_passes_through_when_already_prefixed(monkeypatch):
    from src.app.services.email import inbound_receiver

    monkeypatch.setattr(inbound_receiver.settings, "ses_inbound_prefix", "inbound/")
    parsed = parse_notification(json.dumps(_ses_notification()))
    assert storage_key_for(parsed) == "inbound/abc123"


def test_storage_key_gains_the_configured_prefix(monkeypatch):
    from src.app.services.email import inbound_receiver

    monkeypatch.setattr(inbound_receiver.settings, "ses_inbound_prefix", "mail/")
    notification = _ses_notification(receipt={"action": {"objectKey": "abc123"}})
    parsed = parse_notification(json.dumps(notification))
    assert storage_key_for(parsed) == "mail/abc123"


def test_storage_key_is_none_without_one():
    notification = _ses_notification(receipt={"action": {"type": "Lambda"}})
    parsed = parse_notification(json.dumps(notification))
    assert storage_key_for(parsed) is None


# ---------------------------------------------------------------------------
# Object store
# ---------------------------------------------------------------------------


class _FakeS3:
    def __init__(self, body=b"raw", error=None):
        self._body = body
        self._error = error
        self.deleted = []

    def get_object(self, Bucket, Key):  # noqa: N803
        if self._error:
            raise self._error
        return {"Body": _FakeBody(self._body)}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.deleted.append(Key)


class _FakeBody:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


def _client_error(code):
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code}}, "GetObject")


def test_fetch_returns_the_raw_message():
    store = InboundMailStore(bucket="b", client=_FakeS3(b"MIME"))
    assert store.fetch("k") == b"MIME"


def test_fetch_without_a_bucket_returns_none():
    assert InboundMailStore(bucket=None, client=_FakeS3()).fetch("k") is None


@pytest.mark.parametrize("code", ["NoSuchKey", "404", "AccessDenied"])
def test_missing_object_returns_none_rather_than_retrying_forever(code):
    """A lifecycle rule or a manual delete can remove the object while the queue
    message is still in flight."""
    store = InboundMailStore(bucket="b", client=_FakeS3(error=_client_error(code)))
    assert store.fetch("k") is None


def test_unexpected_s3_error_propagates():
    """A real fault should surface and be retried, not be mistaken for absence."""
    store = InboundMailStore(bucket="b", client=_FakeS3(error=_client_error("SlowDown")))
    with pytest.raises(Exception):
        store.fetch("k")


def test_delete_removes_the_stored_copy():
    """The body lives encrypted in the database after processing; a second
    plaintext copy widens the PHI footprint for no benefit."""
    fake = _FakeS3()
    InboundMailStore(bucket="b", client=fake).delete("k")
    assert fake.deleted == ["k"]
