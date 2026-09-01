import { LocationSelector } from "@/components/location-selector"
import { useAppShell } from "@/components/foundation/AppShell"
import { Link, useLocation } from "react-router-dom"
import { useAuth } from "@/context/AuthContext"
import { useInstitution } from "@/context/InstitutionContext"
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

type NavItemDef = { title: string; url: string; asset: string; exact?: boolean }

// Admin-only nav items
const adminNav: NavItemDef[] = [
    {
        title: "Admin Dashboard",
        url: "/admin",
        asset: dashboardIcon,
        exact: true,
    },
    {
        title: "Institutions",
        url: "/institutions",
        asset: adminIcon,
    },
    {
        title: "Groups",
        url: "/groups",
        asset: groupsIcon,
    },
    {
        title: "Users",
        url: "/admin/users",
        asset: usersIcon,
    },
    {
        // Platform-wide patient conversations. The page filters by practice
        // and location; the API is what actually enforces the span.
        title: "Inbox",
        url: "/inbox",
        asset: inboxIcon,
    },
    {
        // Both email admin surfaces ask which practice first.
        title: "Campaign Emails",
        url: "/institution-admin/campaign-email-templates",
        asset: campaignEmailsIcon,
    },
    {
        title: "Sending Addresses",
        url: "/institution-admin/email-sending-address",
        asset: sendingAddressIcon,
    },
    {
        title: "Phone Numbers",
        url: "/admin/twilio",
        asset: telephonyIcon,
    },
    {
        title: "Audit Logs",
        url: "/admin/audit-logs",
        asset: auditIcon,
    },
]

const institutionAdminNav: NavItemDef[] = [
    {
        title: "Institution Admin",
        url: "/institution-admin",
        asset: adminIcon,
        exact: true,
    },
    {
        title: "User Management",
        url: "/institution-admin/users",
        asset: usersIcon,
    },
    {
        title: "Dashboard",
        url: "/dashboard",
        asset: dashboardIcon,
    },
    {
        title: "Calls",
        url: "/calls",
        asset: callsIcon,
    },
    {
        title: "Callback Queue",
        url: "/callbacks",
        asset: callbackQueueIcon,
    },
    {
        title: "Call Statuses",
        url: "/institution-admin/call-statuses",
        asset: workflowIcon,
    },
    {
        title: "Campaigns",
        url: "/institution-admin/campaigns",
        asset: campaignsIcon,
    },
    {
        title: "Appointment Sync",
        url: "/institution-admin/appointment-sync",
        asset: appointmentSyncIcon,
    },
    {
        title: "DNC Patients",
        url: "/institution-admin/do-not-contact",
        asset: patientsIcon,
    },
]

const locationAdminNav: NavItemDef[] = [
    {
        title: "Management",
        url: "/location-admin",
        asset: adminIcon,
        exact: true,
    },
    {
        title: "Dashboard",
        url: "/dashboard",
        asset: dashboardIcon,
    },
    {
        title: "Calls",
        url: "/calls",
        asset: callsIcon,
    },
    {
        title: "Callback Queue",
        url: "/callbacks",
        asset: callbackQueueIcon,
    },
    {
        title: "Call Statuses",
        url: "/institution-admin/call-statuses",
        asset: workflowIcon,
    },
    {
        title: "Appointment Sync",
        url: "/institution-admin/appointment-sync",
        asset: appointmentSyncIcon,
    },
]

const staffNav: NavItemDef[] = [
    {
        title: "Dashboard",
        url: "/dashboard",
        asset: dashboardIcon,
    },
    {
        title: "Calls",
        url: "/calls",
        asset: callsIcon,
    },
    {
        title: "Callback Queue",
        url: "/callbacks",
        asset: callbackQueueIcon,
    },
]

// Group oversight (DSO) — read-only cross-practice dashboard.
const groupNav: NavItemDef[] = [
    {
        title: "Group Dashboard",
        url: "/group",
        asset: groupsIcon,
        exact: true,
    },
    {
        // Activity figures only — the API refuses this role conversation
        // content, so the page renders volumes and response times.
        title: "Conversations",
        url: "/inbox",
        asset: inboxIcon,
    },
]

// Institution setup nav items
const navSetup: NavItemDef[] = [
    {
        title: "Setup Overview",
        url: "/setup",
        asset: settingsIcon,
        exact: true,
    },
    {
        title: "Appointment Types",
        url: "/setup/appointment-types",
        asset: appointmentTypesIcon,
    },
    {
        title: "Reasons",
        url: "/setup/reasons",
        asset: schedulingIcon,
    },
    {
        title: "Providers & Scheduling",
        url: "/setup/providers",
        asset: schedulingIcon,
    },
    {
        title: "Operatories",
        url: "/setup/operatories",
        asset: operatoriesIcon,
    },
    {
        title: "Insurance Plans",
        url: "/setup/insurance-plans",
        asset: insurancePlansIcon,
    },
    {
        title: "Audit Logs",
        url: "/setup/audit-logs",
        asset: auditIcon,
    },
]

function NavItem({ item, isActive }: { item: NavItemDef; isActive: boolean }) {
    const { closeMobile } = useAppShell()

    return (
        <li>
            <Link
                to={item.url}
                className="shell-nav-link"
                data-active={isActive ? "true" : "false"}
                aria-current={isActive ? "page" : undefined}
                title={item.title}
                onClick={closeMobile}
            >
                <span className="shell-nav-icon" aria-hidden="true"><img src={item.asset} alt="" /></span>
                <span className="shell-nav-text">{item.title}</span>
            </Link>
        </li>
    )
}

export function AppSidebar() {
    const { user } = useAuth();
    const { hasPms, pmsType } = useInstitution();
    const location = useLocation();

    const isAdmin = user?.role === "SUPER_ADMIN";
    const isInstitution =
        user?.role === "INSTITUTION_ADMIN" ||
        user?.role === "LOCATION_ADMIN" ||
        user?.role === "STAFF";
    const mainNav = isAdmin
        ? adminNav
        : user?.role === "INSTITUTION_ADMIN"
            ? institutionAdminNav
            : user?.role === "GROUP_ADMIN"
                ? groupNav
                : user?.role === "LOCATION_ADMIN"
                    ? locationAdminNav
                    : staffNav;
    const pmsSetupNav = pmsType === "gotracker"
        ? navSetup
        : navSetup.filter((item) => item.url !== "/setup/reasons")
    const setupNav = user?.role === "STAFF"
        ? pmsSetupNav.filter((item) => item.url !== "/setup" && item.url !== "/setup/audit-logs")
        : pmsSetupNav;

    return (
        <aside className="shell-sidebar" aria-label="Primary navigation">
            <div className="shell-sidebar-inner">
                {user?.role === "INSTITUTION_ADMIN" && (
                    <section className="shell-nav-section">
                        <h2 className="shell-nav-label">Active Location</h2>
                        <div className="shell-location-wrap">
                            <LocationSelector />
                        </div>
                    </section>
                )}
                <section className="shell-nav-section">
                    <h2 className="shell-nav-label">Menu</h2>
                    <ul className="shell-nav-list">
                            {mainNav.map((item) => (
                                <NavItem
                                    key={item.title}
                                    item={item}
                                    isActive={
                                        item.exact
                                            ? location.pathname === item.url
                                            : location.pathname === item.url || location.pathname.startsWith(item.url + "/")
                                    }
                                />
                            ))}
                            {/* No-PMS tenants are call-intelligence-only: surface the
                                patient directory in place of Practice Setup. */}
                            {isInstitution && !hasPms && (
                                <NavItem
                                    item={{ title: "Patients", url: "/patients", asset: patientsIcon }}
                                    isActive={location.pathname === "/patients" || location.pathname.startsWith("/patients/")}
                                />
                            )}
                    </ul>
                </section>
                {isInstitution && hasPms && (
                    <section className="shell-nav-section">
                        <h2 className="shell-nav-label">Practice Setup</h2>
                        <ul className="shell-nav-list">
                                {setupNav.map((item) => (
                                    <NavItem
                                        key={item.title}
                                        item={item}
                                        isActive={
                                            item.exact
                                                ? location.pathname === item.url
                                                : location.pathname === item.url || location.pathname.startsWith(item.url + "/")
                                        }
                                    />
                                ))}
                        </ul>
                    </section>
                )}
                {isInstitution && (
                    <section className="shell-nav-section">
                        <h2 className="shell-nav-label">Settings</h2>
                        <ul className="shell-nav-list">
                                {user?.role === "INSTITUTION_ADMIN" && (
                                    <NavItem
                                        item={{
                                            title: "Email Templates",
                                            url: "/institution-admin/email-templates",
                                            asset: emailTemplatesIcon,
                                        }}
                                        isActive={location.pathname === "/institution-admin/email-templates"}
                                    />
                                )}
                                {/* Every role reaches the inbox; the API narrows what each
                                    one sees, and gives group admins figures rather than
                                    patient conversations. */}
                                <NavItem
                                    item={{
                                        title: "Inbox",
                                        url: "/inbox",
                                        asset: inboxIcon,
                                    }}
                                    isActive={location.pathname.startsWith("/inbox")}
                                />
                                {user?.role === "INSTITUTION_ADMIN" && (
                                    <NavItem
                                        item={{
                                            title: "Campaign Emails",
                                            url: "/institution-admin/campaign-email-templates",
                                            asset: campaignEmailsIcon,
                                        }}
                                        isActive={location.pathname.startsWith("/institution-admin/campaign-email-templates")}
                                    />
                                )}
                                {user?.role === "INSTITUTION_ADMIN" && (
                                    <NavItem
                                        item={{
                                            title: "Sending Address",
                                            url: "/institution-admin/email-sending-address",
                                            asset: sendingAddressIcon,
                                        }}
                                        isActive={location.pathname.startsWith("/institution-admin/email-sending-address")}
                                    />
                                )}
                                <NavItem
                                    item={{
                                        title: "Email Preferences",
                                        url: "/notification-preferences",
                                        asset: emailPreferencesIcon,
                                    }}
                                    isActive={location.pathname === "/notification-preferences"}
                                />
                                {user?.role === "INSTITUTION_ADMIN" && (
                                    <NavItem
                                        item={{
                                            title: "Settings",
                                            url: "/institution-admin/settings",
                                            asset: settingsIcon,
                                        }}
                                        isActive={location.pathname === "/institution-admin/settings" || location.pathname.startsWith("/institution-admin/settings")}
                                    />
                                )}
                        </ul>
                    </section>
                )}
            </div>
        </aside>
    )
}
