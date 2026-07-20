"""SQLAlchemy models for multi-institution architecture."""

from src.app.models.audit_log import AuditAction, AuditActor, AuditLog, AuditOutcome
from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationStepStatus,
    AutomationTimerStatus,
    AutomationWorkflow,
    AutomationWorkflowEvent,
    AutomationWorkflowRun,
    AutomationWorkflowStatus,
    AutomationWorkflowStepExecution,
    AutomationWorkflowTimer,
    AutomationWorkflowVersion,
)
from src.app.models.appointment_working_set import (  # noqa: F401 — model registration
    AppointmentWorkingSet,
)
from src.app.models.call import Call, CallDirection, CallStatus, PatientStatus
from src.app.models.campaign_response import (  # noqa: F401 — model registration
    CampaignResponseEvent,
    CampaignStaffHandoff,
)
from src.app.models.campaign_analytics import (  # noqa: F401 — model registration
    CampaignMetricsDaily,
    CampaignOutcomeDefinition,
)
from src.app.models.campaign_audience import (  # noqa: F401 — model registration
    CampaignAudienceDefinition,
    CampaignAudiencePreview,
)
from src.app.models.inbound_sms_message import (  # noqa: F401 — model registration
    InboundSmsMessage,
)
from src.app.models.nexhealth_webhook_event import (  # noqa: F401 — model registration
    NexHealthWebhookEvent,
    NexHealthWebhookStatus,
)
from src.app.models.patient_working_set import (  # noqa: F401 — model registration
    PatientWorkingSet,
)
from src.app.models.nexhealth_sync_status import (  # noqa: F401 — model registration
    NexHealthSyncStatus,
)
from src.app.models.nexhealth_webhook_subscription import (  # noqa: F401 — model registration
    NexHealthWebhookSubscription,
    NexHealthWebhookSubscriptionStatus,
)
from src.app.models.call_metrics_daily import (  # noqa: F401 — model registration
    CallMetricsDaily,
    NULL_LOCATION_SENTINEL,
)
from src.app.models.contact import Contact
from src.app.models.contact_location_access import ContactLocationAccess
from src.app.models.custom_field import (
    CustomFieldDefinition,
    CustomFieldValue,
    EntityType,
    FieldType,
    RetellSource,
)
from src.app.models.institution import Institution
from src.app.models.institution_group import InstitutionGroup
from src.app.models.workflow_status import WorkflowStatus
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
from src.app.models.retell_function_invocation import (
    RetellFunctionInvocation,
    RetellFunctionStatus,
)
from src.app.models.sms_history_log import SmsHistoryLog, SmsStatus
from src.app.models.sms_consent import (
    ConsentBasis,
    ConsentChannel,
    ConsentRecord,
    ConsentSource,
    ConsentStatus,
    DncScope,
    DoNotContact,
    SmsSuppression,
)
from src.app.models.dead_letter_event import DeadLetterEvent, DeadLetterStatus
from src.app.models.outbound_halt import OutboundEmergencyHalt
from src.app.models.usage_cost_rollup import (  # noqa: F401 — model registration
    UsageCostRollup,
)
from src.app.models.usage_event import (
    UsageChannel,
    UsageDirection,
    UsageEvent,
    UsageProvider,
)
from src.app.models.outbound_voice import (
    OutboundVoiceProfile,
    VoiceAttemptStatus,
    WorkflowVoiceAttempt,
)

__all__ = [
    "Institution",
    "InstitutionGroup",
    "WorkflowStatus",
    "AutomationWorkflow",
    "AutomationWorkflowVersion",
    "AutomationWorkflowRun",
    "AutomationWorkflowStepExecution",
    "AutomationWorkflowTimer",
    "AutomationWorkflowEvent",
    "AutomationWorkflowStatus",
    "AutomationRunStatus",
    "AutomationStepStatus",
    "AutomationTimerStatus",
    "InstitutionLocation",
    "InstitutionProvider",
    "InstitutionAppointmentType",
    "InstitutionDescriptor",
    "InstitutionOperatory",
    "InstitutionLocationTransferNumber",
    "InsurancePlan",
    "Contact",
    "ContactLocationAccess",
    "Call",
    "CallStatus",
    "CallDirection",
    "PatientStatus",
    "CampaignResponseEvent",
    "CampaignStaffHandoff",
    "CampaignMetricsDaily",
    "CampaignOutcomeDefinition",
    "CampaignAudienceDefinition",
    "CampaignAudiencePreview",
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
    "RetellFunctionInvocation",
    "RetellFunctionStatus",
    "EmailTemplate",
    "EmailTemplateType",
    "ExternalNotificationRecipient",
    "Notification",
    "NotificationType",
    "UserEmailNotificationPreference",
    "SmsHistoryLog",
    "SmsStatus",
    "ConsentBasis",
    "ConsentChannel",
    "DncScope",
    "ConsentRecord",
    "ConsentSource",
    "ConsentStatus",
    "DoNotContact",
    "SmsSuppression",
    "DeadLetterEvent",
    "DeadLetterStatus",
    "OutboundEmergencyHalt",
    "AppointmentWorkingSet",
    "InboundSmsMessage",
    "NexHealthWebhookEvent",
    "NexHealthWebhookStatus",
    "PatientWorkingSet",
    "NexHealthSyncStatus",
    "NexHealthWebhookSubscription",
    "NexHealthWebhookSubscriptionStatus",
    "UsageCostRollup",
    "UsageEvent",
    "UsageChannel",
    "UsageDirection",
    "UsageProvider",
    "OutboundVoiceProfile",
    "WorkflowVoiceAttempt",
    "VoiceAttemptStatus",
]
