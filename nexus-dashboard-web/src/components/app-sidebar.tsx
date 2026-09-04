import {
    Sidebar,
    SidebarContent,
    SidebarGroup,
    SidebarGroupContent,
    SidebarGroupLabel,
    SidebarMenu,
    SidebarMenuButton,
    SidebarMenuItem,
    SidebarRail,
} from "@/components/ui/sidebar"
import { LocationSelector } from "@/components/location-selector"
import { type PageArtName } from "@/assets/icons"
import { Art } from "@/components/Art"
import { Link, useLocation } from "react-router-dom"
import { useAuth } from "@/context/AuthContext"
import { useInstitution } from "@/context/InstitutionContext"

type NavItemDef = { title: string; url: string; exact?: boolean }

// Illustrated artwork for the nav, keyed by route rather than by nav title:
// the same route appears in several role-specific nav lists and must always
// carry the same icon. Every route is intentionally required here so a new
// sidebar item cannot silently fall back to an inconsistent glyph.
const NAV_ART: Record<string, PageArtName> = {
    "/admin": "dashboard",
    "/admin/audit-logs": "audit",
    "/admin/twilio": "telephony",
    "/admin/users": "users",
    "/callbacks": "callbackQueue",
    "/calls": "calls",
    "/contacts": "users",
    "/dashboard": "dashboard",
    "/group": "groups",
    "/groups": "groups",
    "/inbox": "inbox",
    "/institution-admin": "admin",
    "/institution-admin/appointment-sync": "appointmentSync",
    "/institution-admin/call-statuses": "workflow",
    "/institution-admin/campaign-email-templates": "campaignEmails",
    "/institution-admin/campaigns": "campaigns",
    "/institution-admin/do-not-contact": "patients",
    "/institution-admin/email-inbox": "inbox",
    "/institution-admin/email-sending-address": "sendingAddress",
    "/institution-admin/email-templates": "emailTemplates",
    "/institution-admin/enquiry-forms": "contactForms",
    "/institution-admin/lead-forms": "leadForms",
    "/institution-admin/quiet-hours-exceptions": "scheduling",
    "/institution-admin/settings": "settings",
    "/institution-admin/sms-templates": "messaging",
    "/institution-admin/users": "users",
    "/institutions": "admin",
    "/location-admin": "admin",
    "/notification-preferences": "emailPreferences",
    "/patients": "patients",
    "/setup": "settings",
    "/setup/appointment-types": "appointmentTypes",
    "/setup/audit-logs": "audit",
    "/setup/insurance-plans": "insurancePlans",
    "/setup/operatories": "operatories",
    "/setup/providers": "scheduling",
    "/setup/reasons": "appointmentTypes",
    "/sms-preferences": "messaging",
    "/undeliverables": "workflow",
}


// Admin-only nav items
const adminNav: NavItemDef[] = [
    {
        title: "Admin Dashboard",
        url: "/admin",
        exact: true,
    },
    {
        title: "Institutions",
        url: "/institutions",
    },
    {
        title: "Groups",
        url: "/groups",
    },
    {
        title: "Users",
        url: "/admin/users",
    },
    {
        // Platform-wide patient conversations. The page filters by practice
        // and location; the API is what actually enforces the span.
        title: "Inbox",
        url: "/inbox",
    },
    {
        // Both email admin surfaces ask which practice first.
        title: "Campaign Emails",
        url: "/institution-admin/campaign-email-templates",
    },
    {
        title: "Sending Addresses",
        url: "/institution-admin/email-sending-address",
    },
    {
        title: "Inbound Email",
        url: "/institution-admin/email-inbox",
    },
    {
        title: "Phone Numbers",
        url: "/admin/twilio",
    },
    {
        title: "Audit Logs",
        url: "/admin/audit-logs",
    },
    {
        title: "Automation issues",
        url: "/undeliverables",
    },
]

const institutionAdminNav: NavItemDef[] = [
    {
        title: "Institution Admin",
        url: "/institution-admin",
        exact: true,
    },
    {
        title: "User Management",
        url: "/institution-admin/users",
    },
    {
        title: "Dashboard",
        url: "/dashboard",
    },
    {
        title: "Calls",
        url: "/calls",
    },
    {
        title: "Callback Queue",
        url: "/callbacks",
    },
    {
        title: "Call Statuses",
        url: "/institution-admin/call-statuses",
    },
    {
        title: "Campaigns",
        url: "/institution-admin/campaigns",
    },
    {
        title: "Appointment Sync",
        url: "/institution-admin/appointment-sync",
    },
    {
        title: "DNC Patients",
        url: "/institution-admin/do-not-contact",
    },
    {
        title: "Quiet Hours",
        url: "/institution-admin/quiet-hours-exceptions",
    },
    {
        title: "Automation issues",
        url: "/undeliverables",
    },
]

const locationAdminNav: NavItemDef[] = [
    {
        title: "Management",
        url: "/location-admin",
        exact: true,
    },
    {
        title: "Dashboard",
        url: "/dashboard",
    },
    {
        title: "Calls",
        url: "/calls",
    },
    {
        title: "Callback Queue",
        url: "/callbacks",
    },
    {
        title: "Call Statuses",
        url: "/institution-admin/call-statuses",
    },
    {
        title: "Appointment Sync",
        url: "/institution-admin/appointment-sync",
    },
    {
        title: "Automation issues",
        url: "/undeliverables",
    },
]

const staffNav: NavItemDef[] = [
    {
        title: "Dashboard",
        url: "/dashboard",
    },
    {
        title: "Calls",
        url: "/calls",
    },
    {
        title: "Callback Queue",
        url: "/callbacks",
    },
]

// Group oversight (DSO) — read-only cross-practice dashboard.
const groupNav: NavItemDef[] = [
    {
        title: "Group Dashboard",
        url: "/group",
        exact: true,
    },
    {
        // Activity figures only — the API refuses this role conversation
        // content, so the page renders volumes and response times.
        title: "Conversations",
        url: "/inbox",
    },
]

// Institution setup nav items
const navSetup: NavItemDef[] = [
    {
        title: "Setup Overview",
        url: "/setup",
        exact: true,
    },
    {
        title: "Appointment Types",
        url: "/setup/appointment-types",
    },
    {
        title: "Reasons",
        url: "/setup/reasons",
    },
    {
        title: "Providers & Scheduling",
        url: "/setup/providers",
    },
    {
        title: "Operatories",
        url: "/setup/operatories",
    },
    {
        title: "Insurance Plans",
        url: "/setup/insurance-plans",
    },
    {
        title: "Audit Logs",
        url: "/setup/audit-logs",
    },
]

function NavItem({ item, isActive }: { item: NavItemDef; isActive: boolean }) {
    const art = NAV_ART[item.url]

    return (
        <SidebarMenuItem>
            <SidebarMenuButton
                asChild
                // Also drives data-active on the rendered element, which is what
                // the nav artwork keys off to stay in colour on the current page.
                isActive={isActive}
                tooltip={item.title}
                className={`
                    relative transition-all duration-150 rounded-md
                    ${isActive
                        ? "bg-sidebar-accent text-sidebar-primary font-semibold before:absolute before:left-0 before:top-1 before:bottom-1 before:w-0.5 before:rounded-full before:bg-sidebar-primary"
                        : "text-sidebar-foreground font-medium hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"
                    }
                `}
            >
                <Link to={item.url} aria-current={isActive ? "page" : undefined}>
                    <Art
                        name={art}
                        className="nav-art size-9 shrink-0 group-data-[collapsible=icon]:size-7"
                    />
                    <span>{item.title}</span>
                </Link>
            </SidebarMenuButton>
        </SidebarMenuItem>
    )
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
    const { user } = useAuth();
    const { hasPms, pmsType } = useInstitution();
    const location = useLocation();

    const isAdmin = user?.role === "SUPER_ADMIN";
    const isNoPmsLocationAdmin = user?.role === "LOCATION_ADMIN" && !hasPms;
    const isInstitution =
        user?.role === "INSTITUTION_ADMIN" ||
        user?.role === "LOCATION_ADMIN" ||
        user?.role === "STAFF";
    const roleMainNav = isAdmin
        ? adminNav
        : user?.role === "INSTITUTION_ADMIN"
            ? institutionAdminNav
            : user?.role === "GROUP_ADMIN"
                ? groupNav
                : user?.role === "LOCATION_ADMIN"
                    ? locationAdminNav
                    : staffNav;
    const mainNav = isNoPmsLocationAdmin
        ? roleMainNav.filter((item) => item.url !== "/institution-admin/appointment-sync")
        : roleMainNav;
    const pmsSetupNav = pmsType === "gotracker"
        ? navSetup
        : navSetup.filter((item) => item.url !== "/setup/reasons")
    const setupNav = user?.role === "STAFF"
        ? pmsSetupNav.filter((item) => item.url !== "/setup" && item.url !== "/setup/audit-logs")
        : pmsSetupNav;

    return (
        <Sidebar
            collapsible="icon"
            className="!top-14 !h-[calc(100svh-3.5rem)]"
            {...props}
        >
            <SidebarContent className="pt-2">
                {user?.role === "INSTITUTION_ADMIN" && (
                    <SidebarGroup className="pt-2">
                        <SidebarGroupLabel className="text-[10px] font-semibold uppercase tracking-widest text-sidebar-foreground/40 px-2 mb-1">
                            Active Location
                        </SidebarGroupLabel>
                        <SidebarGroupContent className="px-2">
                            <LocationSelector />
                        </SidebarGroupContent>
                    </SidebarGroup>
                )}
                <SidebarGroup>
                    <SidebarGroupLabel className="text-[10px] font-semibold uppercase tracking-widest text-sidebar-foreground/40 px-2 mb-1 mt-2">
                        Menu
                    </SidebarGroupLabel>
                    <SidebarGroupContent>
                        <SidebarMenu>
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
                            {isInstitution && (
                                <NavItem
                                    item={{ title: "Contacts", url: "/contacts" }}
                                    isActive={location.pathname === "/contacts" || location.pathname.startsWith("/contacts/") || location.pathname === "/enquiries"}
                                />
                            )}
                            {isInstitution && hasPms && (
                                <NavItem
                                    item={{ title: "Patients", url: "/patients" }}
                                    isActive={location.pathname === "/patients" || location.pathname.startsWith("/patients/")}
                                />
                            )}
                        </SidebarMenu>
                    </SidebarGroupContent>
                </SidebarGroup>
                {isInstitution && hasPms && (
                    <SidebarGroup>
                        <SidebarGroupLabel className="text-[10px] font-semibold uppercase tracking-widest text-sidebar-foreground/40 px-2 mb-1">
                            Practice Setup
                        </SidebarGroupLabel>
                        <SidebarGroupContent>
                            <SidebarMenu>
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
                            </SidebarMenu>
                        </SidebarGroupContent>
                    </SidebarGroup>
                )}
                {isInstitution && (
                    <SidebarGroup>
                        <SidebarGroupLabel className="text-[10px] font-semibold uppercase tracking-widest text-sidebar-foreground/40 px-2 mb-1">
                            Settings
                        </SidebarGroupLabel>
                        <SidebarGroupContent>
                            <SidebarMenu>
                                {user?.role === "INSTITUTION_ADMIN" && (
                                    <NavItem
                                        item={{
                                            title: "Email Templates",
                                            url: "/institution-admin/email-templates",
                                        }}
                                        isActive={location.pathname === "/institution-admin/email-templates"}
                                    />
                                )}
                                {user?.role === "INSTITUTION_ADMIN" && (
                                    <NavItem
                                        item={{
                                            title: "Inbound Email",
                                            url: "/institution-admin/email-inbox",
                                        }}
                                        isActive={location.pathname.startsWith("/institution-admin/email-inbox")}
                                    />
                                )}
                                {user?.role === "INSTITUTION_ADMIN" && (
                                    <NavItem
                                        item={{
                                            title: "Contact Forms",
                                            url: "/institution-admin/enquiry-forms",
                                        }}
                                        isActive={location.pathname === "/institution-admin/enquiry-forms"}
                                    />
                                )}
                                {user?.role === "INSTITUTION_ADMIN" && (
                                    <NavItem
                                        item={{
                                            title: "Lead Forms",
                                            url: "/institution-admin/lead-forms",
                                        }}
                                        isActive={location.pathname.startsWith(
                                            "/institution-admin/lead-forms",
                                        )}
                                    />
                                )}
                                {user?.role === "INSTITUTION_ADMIN" && !hasPms && (
                                    <NavItem
                                        item={{
                                            title: "SMS Templates",
                                            url: "/institution-admin/sms-templates",
                                        }}
                                        isActive={location.pathname === "/institution-admin/sms-templates"}
                                    />
                                )}
                                {!isNoPmsLocationAdmin && (
                                    <NavItem
                                        item={{
                                            title: "Inbox",
                                            url: "/inbox",
                                        }}
                                        isActive={location.pathname.startsWith("/inbox")}
                                    />
                                )}
                                {user?.role === "INSTITUTION_ADMIN" && (
                                    <NavItem
                                        item={{
                                            title: "Campaign Emails",
                                            url: "/institution-admin/campaign-email-templates",
                                        }}
                                        isActive={location.pathname.startsWith("/institution-admin/campaign-email-templates")}
                                    />
                                )}
                                {(user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN") && (
                                    <NavItem
                                        item={{
                                            title: "Sending Address",
                                            url: "/institution-admin/email-sending-address",
                                        }}
                                        isActive={location.pathname.startsWith("/institution-admin/email-sending-address")}
                                    />
                                )}
                                <NavItem
                                    item={{
                                        title: "Email Preferences",
                                        url: "/notification-preferences",
                                    }}
                                    isActive={location.pathname === "/notification-preferences"}
                                />
                                {!hasPms && (user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN") && (
                                    <NavItem
                                        item={{
                                            title: "SMS Preferences",
                                            url: "/sms-preferences",
                                        }}
                                        isActive={location.pathname === "/sms-preferences"}
                                    />
                                )}
                                {user?.role === "INSTITUTION_ADMIN" && (
                                    <NavItem
                                        item={{
                                            title: "Settings",
                                            url: "/institution-admin/settings",
                                        }}
                                        isActive={location.pathname === "/institution-admin/settings" || location.pathname.startsWith("/institution-admin/settings")}
                                    />
                                )}
                            </SidebarMenu>
                        </SidebarGroupContent>
                    </SidebarGroup>
                )}
            </SidebarContent>
            <SidebarRail />
        </Sidebar>
    )
}
