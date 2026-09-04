// Illustrated page artwork.
//
// These are detailed multi-colour illustrations, not line glyphs. They read
// well from about 40px up and turn to mush in a dense nav row, so they belong
// in page headers and empty states — lucide stays the icon set for sidebar
// navigation, buttons and inline affordances.
//
// Each import becomes its own emitted asset, so listing them all here costs a
// URL string in the bundle; the browser only fetches the ones a page renders.
import admin from "./presentation/admin.png"
import appointmentSync from "./presentation/appointment-sync.png"
import appointmentTypes from "./presentation/appointment-types.png"
import audit from "./presentation/audit.png"
import callbackQueue from "./presentation/callback-queue.png"
import calls from "./presentation/calls.png"
import campaignEmails from "./presentation/campaign-emails-v2.png"
import campaigns from "./presentation/campaigns-outlined.png"
import contactForms from "./presentation/contact-forms.png"
import dashboard from "./presentation/dashboard.png"
import emailPreferences from "./presentation/email-preferences-v2.png"
import emailTemplates from "./presentation/email-templates-v2.png"
import groups from "./presentation/groups.png"
import inbox from "./presentation/inbox.png"
import insurancePlans from "./presentation/insurance-plans.png"
import leadForms from "./presentation/lead-forms.png"
import messaging from "./presentation/messaging.png"
import operatories from "./presentation/operatories.png"
import passkey from "./passkey-shield-v2.png"
import patients from "./presentation/patients-outlined.png"
import scheduling from "./presentation/scheduling.png"
import sendingAddress from "./presentation/sending-address-v2.png"
import settings from "./presentation/settings.png"
import telephony from "./presentation/telephony.png"
import users from "./presentation/users-outlined.png"
import workflow from "./presentation/workflow.png"

export const pageArt = {
    admin,
    appointmentSync,
    appointmentTypes,
    audit,
    callbackQueue,
    calls,
    campaignEmails,
    campaigns,
    contactForms,
    dashboard,
    emailPreferences,
    emailTemplates,
    groups,
    inbox,
    insurancePlans,
    leadForms,
    messaging,
    operatories,
    passkey,
    patients,
    scheduling,
    sendingAddress,
    settings,
    telephony,
    users,
    workflow,
} as const

export type PageArtName = keyof typeof pageArt
