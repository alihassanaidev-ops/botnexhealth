"""Fetch received mail from the provider's storage.

SES writes the full MIME to S3 and publishes a notification. The notification
carries metadata only — recipients, verdicts, an object key — never the body,
because a notification is capped at 150 KB while a message may be 40 MB. So the
worker reads the notification, then fetches the object.

That split is also why this uses a queue rather than an HTTPS endpoint. A queue
holds mail through a deploy or an outage instead of retrying at a service that
is not listening, and there is no signature-verification surface to get wrong.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from src.app.config import settings
from src.app.services.email.inbound_router import InboundVerdicts

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InboundNotification:
    """What the provider told us before we fetch the message itself."""

    message_id: str | None
    recipients: list[str]
    storage_key: str | None
    verdicts: InboundVerdicts
    raw: dict[str, Any]


def parse_notification(payload: str | dict) -> InboundNotification | None:
    """Read an SES receipt notification, tolerating the SNS envelope.

    Queue messages arrive wrapped by SNS, so the useful JSON is a string inside
    a string. Anything unrecognised returns None rather than raising: a
    malformed notification must not stall the queue behind it.
    """
    try:
        body = json.loads(payload) if isinstance(payload, str) else dict(payload)
    except Exception:  # noqa: BLE001
        logger.warning("inbound notification is not valid JSON")
        return None

    # Unwrap the SNS envelope when present.
    if "Message" in body and "Type" in body:
        try:
            body = json.loads(body["Message"])
        except Exception:  # noqa: BLE001
            logger.warning("inbound SNS envelope did not contain valid JSON")
            return None

    receipt = body.get("receipt") or {}
    mail = body.get("mail") or {}
    if not receipt and not mail:
        return None

    action = receipt.get("action") or {}
    key = action.get("objectKey") or action.get("objectKeyPrefix")

    return InboundNotification(
        message_id=mail.get("messageId"),
        recipients=[str(r).lower() for r in (receipt.get("recipients") or mail.get("destination") or [])],
        storage_key=key,
        verdicts=InboundVerdicts(
            spam=_verdict(receipt, "spamVerdict"),
            virus=_verdict(receipt, "virusVerdict"),
            spf=_verdict(receipt, "spfVerdict"),
            dkim=_verdict(receipt, "dkimVerdict"),
            dmarc=_verdict(receipt, "dmarcVerdict"),
        ),
        raw=body,
    )


def _verdict(receipt: dict, name: str) -> str | None:
    value = (receipt.get(name) or {}).get("status")
    return str(value) if value else None


class InboundMailStore:
    """Reads raw MIME out of the bucket the receipt rule writes to."""

    def __init__(self, bucket: str | None = None, client=None) -> None:  # noqa: ANN001
        self._bucket = bucket or settings.ses_inbound_bucket
        self._client = client

    def _s3(self):  # noqa: ANN202
        if self._client is None:
            import boto3

            self._client = boto3.client("s3")
        return self._client

    def fetch(self, key: str) -> bytes | None:
        """Return the raw message, or None when it cannot be read.

        A missing object is not retried forever: the notification is the record
        that mail arrived, and a lifecycle rule or a manual deletion can remove
        the object while the queue message is still in flight.
        """
        if not self._bucket or not key:
            return None
        from botocore.exceptions import ClientError

        try:
            response = self._s3().get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "AccessDenied"):
                logger.warning("inbound message object unavailable: key=%s (%s)", key, code)
                return None
            raise

    def delete(self, key: str) -> None:
        """Remove the stored message once its contents are persisted.

        The body lives encrypted in the database after processing, so leaving a
        second plaintext copy in object storage widens the PHI footprint for no
        benefit.
        """
        if not self._bucket or not key:
            return
        from botocore.exceptions import ClientError

        try:
            self._s3().delete_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:  # noqa: BLE001 — cleanup is best-effort
            logger.warning("could not delete inbound object %s: %s", key, exc)


def storage_key_for(notification: InboundNotification) -> str | None:
    """Resolve the object key, filling in the prefix the rule was configured with."""
    key = notification.storage_key
    if not key:
        return None
    prefix = settings.ses_inbound_prefix or ""
    if prefix and not key.startswith(prefix):
        return f"{prefix.rstrip('/')}/{key.lstrip('/')}"
    return key
