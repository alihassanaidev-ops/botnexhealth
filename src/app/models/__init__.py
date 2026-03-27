"""SQLAlchemy models for multi-institution architecture."""

from src.app.models.audit_log import AuditAction, AuditActor, AuditLog, AuditOutcome
from src.app.models.call import Call, CallDirection, CallStatus, PatientStatus
from src.app.models.contact import Contact
from src.app.models.custom_field import (
    CustomFieldDefinition,
    CustomFieldValue,
    EntityType,
    FieldType,
    RetellSource,
)
from src.app.models.institution import Institution
from src.app.models.institution_appointment_type import InstitutionAppointmentType
from src.app.models.institution_descriptor import InstitutionDescriptor
from src.app.models.institution_location import InstitutionLocation
from src.app.models.institution_operatory import InstitutionOperatory
from src.app.models.institution_provider import InstitutionProvider
from src.app.models.institution_location_transfer_number import InstitutionLocationTransferNumber
from src.app.models.insurance_plan import InsurancePlan
from src.app.models.location_break import LocationBreak
from src.app.models.location_operating_hours import LocationOperatingHours
from src.app.models.email_template import EmailTemplate, EmailTemplateType
from src.app.models.external_notification_recipient import ExternalNotificationRecipient
from src.app.models.notification import Notification, NotificationType
from src.app.models.user_email_notification_preference import UserEmailNotificationPreference
from src.app.models.retell_webhook_event import RetellWebhookEvent, RetellWebhookStatus
from src.app.models.sms_history_log import SmsHistoryLog, SmsStatus

__all__ = [
    "Institution",
    "InstitutionLocation",
    "InstitutionProvider",
    "InstitutionAppointmentType",
    "InstitutionDescriptor",
    "InstitutionOperatory",
    "InstitutionLocationTransferNumber",
    "InsurancePlan",
    "Contact",
    "Call",
    "CallStatus",
    "CallDirection",
    "PatientStatus",
    "CustomFieldDefinition",
    "CustomFieldValue",
    "EntityType",
    "FieldType",
    "RetellSource",
    "AuditLog",
    "AuditAction",
    "AuditActor",
    "AuditOutcome",
    "LocationOperatingHours",
    "LocationBreak",
    "RetellWebhookEvent",
    "RetellWebhookStatus",
    "EmailTemplate",
    "EmailTemplateType",
    "ExternalNotificationRecipient",
    "Notification",
    "NotificationType",
    "UserEmailNotificationPreference",
    "SmsHistoryLog",
    "SmsStatus",
]
