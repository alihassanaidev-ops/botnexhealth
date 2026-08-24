"""Outbound email provider seam."""

from src.app.services.email.sender import (
    EmailMessage,
    EmailSender,
    EmailSendError,
    EmailSendResult,
    ResendSender,
    SesSender,
    get_patient_email_sender,
    get_transactional_email_sender,
)

__all__ = [
    "EmailMessage",
    "EmailSendError",
    "EmailSendResult",
    "EmailSender",
    "ResendSender",
    "SesSender",
    "get_patient_email_sender",
    "get_transactional_email_sender",
]
