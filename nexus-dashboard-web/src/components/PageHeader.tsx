import type { LucideIcon } from "lucide-react"
import type { ReactNode } from "react"
import { cn } from "@/lib/utils"
import dashboardIcon from "@/assets/icons/presentation/dashboard.png"
import callsIcon from "@/assets/icons/presentation/calls.png"
import patientsIcon from "@/assets/icons/presentation/patients-outlined.png"
import campaignsIcon from "@/assets/icons/presentation/campaigns-outlined.png"
import schedulingIcon from "@/assets/icons/presentation/scheduling.png"
import settingsIcon from "@/assets/icons/presentation/settings.png"
import adminIcon from "@/assets/icons/presentation/admin.png"
import usersIcon from "@/assets/icons/presentation/users-outlined.png"
import auditIcon from "@/assets/icons/presentation/audit.png"
import workflowIcon from "@/assets/icons/presentation/workflow.png"
import groupsIcon from "@/assets/icons/presentation/groups.png"
import inboxIcon from "@/assets/icons/presentation/inbox.png"
import telephonyIcon from "@/assets/icons/presentation/telephony.png"
import callbackQueueIcon from "@/assets/icons/presentation/callback-queue.png"
import emailTemplatesIcon from "@/assets/icons/presentation/email-templates-v2.png"
import campaignEmailsIcon from "@/assets/icons/presentation/campaign-emails-v2.png"
import sendingAddressIcon from "@/assets/icons/presentation/sending-address-v2.png"
import emailPreferencesIcon from "@/assets/icons/presentation/email-preferences-v2.png"
import appointmentTypesIcon from "@/assets/icons/presentation/appointment-types.png"
import operatoriesIcon from "@/assets/icons/presentation/operatories.png"
import insurancePlansIcon from "@/assets/icons/presentation/insurance-plans.png"
import appointmentSyncIcon from "@/assets/icons/presentation/appointment-sync.png"
import "./foundation/page-header.css"

const PAGE_ART: Record<string, string> = {
    Dashboard: dashboardIcon,
    "Institution Admin Panel": adminIcon,
    "Admin Dashboard": adminIcon,
    Institutions: adminIcon,
    "Location Admin": adminIcon,
    "Group Dashboard": groupsIcon,
    Groups: groupsIcon,
    Calls: callsIcon,
    "Callback Queue": callbackQueueIcon,
    "Call Statuses": workflowIcon,
    Patients: patientsIcon,
    "DNC Patients": patientsIcon,
    Campaigns: campaignsIcon,
    "Campaign Email Templates": campaignEmailsIcon,
    "Appointment Sync": appointmentSyncIcon,
    "Appointment Types": appointmentTypesIcon,
    "Providers & Scheduling": schedulingIcon,
    Operatories: operatoriesIcon,
    Reasons: schedulingIcon,
    Security: settingsIcon,
    Settings: settingsIcon,
    "Institution Settings": settingsIcon,
    "Setup Overview": settingsIcon,
    "Insurance Plans": insurancePlansIcon,
    "Setup overview": settingsIcon,
    "Custom Fields": settingsIcon,
    "Audit Logs": auditIcon,
    "Platform Audit Logs": auditIcon,
    "Notification Preferences": emailPreferencesIcon,
    "Email Preferences": emailPreferencesIcon,
    "Email Templates": emailTemplatesIcon,
    "Email Sending Address": sendingAddressIcon,
    Inbox: inboxIcon,
    "User Management": usersIcon,
    "Institution User Management": usersIcon,
    Users: usersIcon,
    "Patient Conversations": patientsIcon,
    "Twilio Phone Numbers": telephonyIcon,
}

// One consistent page heading for every page: an icon (matching the sidebar
// nav), the title, an optional description, and a right-aligned actions slot.
// Replaces the ad-hoc mix of h1/h2, text-2xl/3xl, and with/without-icon headings.
export function PageHeader({
    icon: Icon,
    art: artOverride,
    title,
    description,
    actions,
    className,
}: {
    icon?: LucideIcon
    art?: string
    title: ReactNode
    description?: ReactNode
    actions?: ReactNode
    className?: string
}) {
    const art = artOverride ?? (typeof title === "string" ? PAGE_ART[title] : undefined)

    return (
        <div className={cn("page-header", className)}>
            <div className="page-header-leading">
                {(art || Icon) && (
                    <span className={cn("page-header-icon", art && "ui-artwork")} aria-hidden="true">
                        {art ? (
                            <img src={art} alt="" />
                        ) : Icon ? (
                            <Icon strokeWidth={1.75} />
                        ) : null}
                    </span>
                )}
                <div className="page-header-copy">
                    <h1>{title}</h1>
                    {description && <div className="page-header-description">{description}</div>}
                </div>
            </div>
            {actions && <div className="page-header-actions">{actions}</div>}
        </div>
    )
}
