"""Parse a received email into the fields routing needs.

Deliberately conservative. This runs on unauthenticated input from the open
internet — anyone can send mail to a catch-all address — so every field is
treated as hostile: headers may be missing, malformed, wrongly encoded, or
absent entirely.

Two behaviours here exist to stop feedback loops rather than to parse anything:
auto-responder detection, and quoted-reply trimming. Without the first, an
out-of-office bouncing against an auto-reply generates mail until someone
notices. Without the second, every reply in a long thread stores another copy of
the whole conversation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from datetime import datetime

logger = logging.getLogger(__name__)

#: Headers that mark a message as machine-generated. Replying to one of these
#: risks a loop where two autoresponders answer each other indefinitely.
_AUTO_HEADERS = (
    "auto-submitted",
    "x-auto-response-suppress",
    "x-autoreply",
    "x-autorespond",
    "precedence",
)
_AUTO_PRECEDENCE = {"bulk", "auto_reply", "junk", "list"}

_STOP_TOKENS = {"stop", "unsubscribe", "remove me", "opt out", "optout", "cancel subscription"}
_CONFIRM_TOKENS = {"yes", "confirm", "confirmed", "y", "ok", "okay", "sounds good", "see you"}
_RESCHEDULE_TOKENS = {"reschedule", "change", "move", "another time", "different time", "postpone"}

#: Common quoted-reply markers. Everything from the first match is dropped.
_QUOTE_MARKERS = (
    re.compile(r"^\s*On .{5,120} wrote:\s*$", re.MULTILINE),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^\s*_{5,}\s*$", re.MULTILINE),
    re.compile(r"^\s*From:\s.+$", re.MULTILINE),
    re.compile(r"^\s*Sent from my \w+", re.MULTILINE),
)
_QUOTE_PREFIX = re.compile(r"^\s*>.*$", re.MULTILINE)

#: Trims our own unsubscribe footer back off an inbound quote.
_FOOTER_MARKER = re.compile(r"\n\s*—\s*\nYou're receiving this because", re.MULTILINE)


@dataclass
class ParsedEmail:
    from_address: str | None = None
    to_addresses: list[str] = field(default_factory=list)
    cc_addresses: list[str] = field(default_factory=list)
    subject: str | None = None
    body_text: str | None = None
    message_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    date: datetime | None = None
    has_attachments: bool = False
    attachment_count: int = 0
    is_auto_reply: bool = False
    #: True for a bounce / delivery report — never reply to one.
    is_bounce: bool = False

    @property
    def all_recipients(self) -> list[str]:
        return [*self.to_addresses, *self.cc_addresses]


def _header(message: EmailMessage, name: str) -> str | None:
    try:
        value = message.get(name)
    except Exception:  # noqa: BLE001 — malformed headers are expected here
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _addresses(message: EmailMessage, name: str) -> list[str]:
    try:
        raw = message.get_all(name, [])
        return [addr.lower() for _, addr in getaddresses(raw) if addr]
    except Exception:  # noqa: BLE001
        return []


def _is_auto_reply(message: EmailMessage) -> bool:
    for header in _AUTO_HEADERS:
        value = (_header(message, header) or "").lower()
        if not value:
            continue
        if header == "auto-submitted":
            # RFC 3834: anything other than "no" is machine-generated.
            if value != "no":
                return True
        elif header == "precedence":
            if value in _AUTO_PRECEDENCE:
                return True
        else:
            return True
    return False


def _is_bounce(message: EmailMessage) -> bool:
    content_type = (_header(message, "content-type") or "").lower()
    if "report-type=delivery-status" in content_type.replace(" ", ""):
        return True
    # A null Return-Path is the classic bounce marker; replying to it would
    # generate another bounce.
    return (_header(message, "return-path") or "").strip() in ("<>", "")


def _extract_text(message: EmailMessage) -> str | None:
    """Prefer the plain-text part; fall back to stripped HTML."""
    try:
        part = message.get_body(preferencelist=("plain",))
        if part is not None:
            return part.get_content()
    except Exception:  # noqa: BLE001
        pass
    try:
        part = message.get_body(preferencelist=("html",))
        if part is not None:
            return _strip_html(part.get_content())
    except Exception:  # noqa: BLE001
        pass
    return None


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t]*")


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = _TAG_RE.sub("", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return _WS_RE.sub("\n", text).strip()


def strip_quoted_reply(body: str | None) -> str:
    """Return just what the person actually wrote.

    Without this, every reply in a thread stores another copy of the whole
    conversation — which for patient mail means storing the same PHI repeatedly.
    """
    if not body:
        return ""
    text = body.replace("\r\n", "\n")

    cut = len(text)
    for marker in _QUOTE_MARKERS:
        match = marker.search(text)
        if match and match.start() < cut:
            cut = match.start()
    footer = _FOOTER_MARKER.search(text)
    if footer and footer.start() < cut:
        cut = footer.start()

    text = text[:cut]
    text = _QUOTE_PREFIX.sub("", text)
    return text.strip()


def classify_intent(body: str | None, *, is_auto_reply: bool = False) -> str:
    """Best-effort read of what the reply is asking for.

    Only used for routing and triage. Anything clinical stays FREE_TEXT so it
    reaches a human rather than being auto-handled.
    """
    from src.app.models.inbound_email_message import InboundEmailIntent

    if is_auto_reply:
        return InboundEmailIntent.AUTO_REPLY.value

    text = (body or "").strip().lower()
    if not text:
        return InboundEmailIntent.FREE_TEXT.value

    # Opt-out is checked first and on the whole body: honouring it late, or not
    # at all because the word sat mid-sentence, is a compliance failure.
    if any(token in text for token in _STOP_TOKENS):
        return InboundEmailIntent.STOP.value

    # Everything else is matched only on a short reply. "yes" inside a paragraph
    # describing a problem is not a confirmation.
    condensed = " ".join(text.split())
    if len(condensed) <= 40:
        if any(condensed == t or condensed.startswith(t + " ") for t in _CONFIRM_TOKENS):
            return InboundEmailIntent.CONFIRM.value

    if any(token in text for token in _RESCHEDULE_TOKENS):
        return InboundEmailIntent.RESCHEDULE.value
    if "?" in text:
        return InboundEmailIntent.QUESTION.value
    return InboundEmailIntent.FREE_TEXT.value


def parse_mime(raw: bytes) -> ParsedEmail:
    """Parse raw MIME. Never raises — a message we cannot read is still evidence."""
    try:
        message: EmailMessage = message_from_bytes(raw, policy=policy.default)  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not parse inbound MIME: %s", exc)
        return ParsedEmail()

    attachments = []
    try:
        attachments = [p for p in message.iter_attachments()]
    except Exception:  # noqa: BLE001
        pass

    date = None
    raw_date = _header(message, "date")
    if raw_date:
        try:
            date = parsedate_to_datetime(raw_date)
        except Exception:  # noqa: BLE001 — malformed Date headers are common
            date = None

    from_addresses = _addresses(message, "from")
    body = _extract_text(message)

    return ParsedEmail(
        from_address=from_addresses[0] if from_addresses else None,
        to_addresses=_addresses(message, "to"),
        cc_addresses=_addresses(message, "cc"),
        subject=_header(message, "subject"),
        body_text=strip_quoted_reply(body),
        message_id=_header(message, "message-id"),
        in_reply_to=_header(message, "in-reply-to"),
        references=_header(message, "references"),
        date=date,
        has_attachments=bool(attachments),
        attachment_count=len(attachments),
        is_auto_reply=_is_auto_reply(message),
        is_bounce=_is_bounce(message),
    )
