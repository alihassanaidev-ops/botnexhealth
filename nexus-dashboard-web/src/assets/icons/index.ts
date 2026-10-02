// @ui-variant refresh — delete this file and ./svg to drop the refreshed UI.
//
// Illustrated page artwork, traced from the original PNGs to SVG so the
// drawings take their colours from the theme instead of carrying them baked
// in. Every fill resolves to one of five CSS variables — --art-ink,
// --art-surface, --art-muted, --art-accent, --art-accent-soft — which
// index.css maps onto the existing theme tokens. Dark mode is a genuine
// recolour rather than a light tile propping up artwork that can't adapt.
//
// Imported with Vite's built-in `?raw` rather than an SVG plugin: the markup
// has to be inline in the document for var() to resolve at all. An
// <img src="....svg"> renders in an isolated context that never sees the
// page's variables, so every fill would collapse to nothing.
//
// Render these with <Art> from "@/components/Art".
import admin from "./svg/admin.svg?raw"
import appointmentSync from "./svg/appointment-sync.svg?raw"
import appointmentTypes from "./svg/appointment-types.svg?raw"
import audit from "./svg/audit.svg?raw"
import awaitingCallback from "./svg/awaiting-callback.svg?raw"
import bookingRate from "./svg/booking-rate.svg?raw"
import callbackQueue from "./svg/callback-queue.svg?raw"
import calls from "./svg/calls.svg?raw"
import campaignEmails from "./svg/campaign-emails-v2.svg?raw"
import campaigns from "./svg/campaigns-outlined.svg?raw"
import contactForms from "./svg/contact-forms-v2.svg?raw"
import contacts from "./svg/contacts.svg?raw"
import dashboard from "./svg/dashboard.svg?raw"
import emailPreferences from "./svg/email-preferences-v2.svg?raw"
import emailTemplates from "./svg/email-templates-v2.svg?raw"
import emergencyCalls from "./svg/emergency-calls.svg?raw"
import groups from "./svg/groups.svg?raw"
import inbox from "./svg/inbox.svg?raw"
import insurancePlans from "./svg/insurance-plans.svg?raw"
import leadForms from "./svg/lead-forms-v2.svg?raw"
import manualBooking from "./svg/manual-booking.svg?raw"
import messaging from "./svg/messaging.svg?raw"
import operatories from "./svg/operatories.svg?raw"
import passkey from "./svg/passkey-shield-v2.svg?raw"
import patients from "./svg/patients-outlined.svg?raw"
import revenueGenerated from "./svg/revenue-generated.svg?raw"
import scheduling from "./svg/scheduling.svg?raw"
import sendingAddress from "./svg/sending-address-v2.svg?raw"
import settings from "./svg/settings.svg?raw"
import staffCostSaved from "./svg/staff-cost-saved.svg?raw"
import telephony from "./svg/telephony.svg?raw"
import providers from "./svg/providers.svg?raw"
import users from "./svg/users.svg?raw"
import workflow from "./svg/workflow.svg?raw"
import netValue from "./svg/net-value.svg?raw"

export const pageArt = {
    admin,
    appointmentSync,
    appointmentTypes,
    audit,
    awaitingCallback,
    bookingRate,
    callbackQueue,
    calls,
    campaignEmails,
    campaigns,
    contactForms,
    contacts,
    dashboard,
    emailPreferences,
    emailTemplates,
    emergencyCalls,
    groups,
    inbox,
    insurancePlans,
    leadForms,
    manualBooking,
    messaging,
    operatories,
    passkey,
    patients,
    revenueGenerated,
    scheduling,
    sendingAddress,
    settings,
    staffCostSaved,
    telephony,
    providers,
    users,
    workflow,
    netValue,
} as const

export type PageArtName = keyof typeof pageArt
