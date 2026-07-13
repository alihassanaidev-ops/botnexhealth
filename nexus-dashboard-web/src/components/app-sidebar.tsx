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
import {
    Home,
    Users,
    Building2,
    CalendarCheck,
    UserCog,
    Armchair,
    LayoutDashboard,
    Megaphone,
    Phone,
    PhoneForwarded,
    Shield,
    ShieldCheck,
    ShieldOff,
    MessageSquare,
    Mail,
    MailCheck,
    Settings,
    ClipboardList,
    Layers,
    Tag,
} from "lucide-react"
import { Link, useLocation } from "react-router-dom"
import { useAuth } from "@/context/AuthContext"
import { useInstitution } from "@/context/InstitutionContext"

type NavItemDef = { title: string; url: string; icon: React.ElementType; exact?: boolean }

// Admin-only nav items
const adminNav: NavItemDef[] = [
    {
        title: "Admin Dashboard",
        url: "/admin",
        icon: LayoutDashboard,
        exact: true,
    },
    {
        title: "Institutions",
        url: "/institutions",
        icon: Users,
    },
    {
        title: "Groups",
        url: "/groups",
        icon: Layers,
    },
    {
        title: "Users",
        url: "/admin/users",
        icon: UserCog,
    },
    {
        title: "Phone Numbers",
        url: "/admin/twilio",
        icon: MessageSquare,
    },
    {
        title: "Audit Logs",
        url: "/admin/audit-logs",
        icon: ShieldCheck,
    },
]

const institutionAdminNav: NavItemDef[] = [
    {
        title: "Institution Admin",
        url: "/institution-admin",
        icon: Building2,
        exact: true,
    },
    {
        title: "User Management",
        url: "/institution-admin/users",
        icon: Users,
    },
    {
        title: "Dashboard",
        url: "/dashboard",
        icon: Home,
    },
    {
        title: "Calls",
        url: "/calls",
        icon: Phone,
    },
    {
        title: "Callback Queue",
        url: "/callbacks",
        icon: PhoneForwarded,
    },
    {
        title: "Call Statuses",
        url: "/institution-admin/call-statuses",
        icon: Tag,
    },
    {
        title: "Campaigns",
        url: "/institution-admin/campaigns",
        icon: Megaphone,
    },
    {
        title: "Do Not Contact",
        url: "/institution-admin/do-not-contact",
        icon: ShieldOff,
    },
]

const locationAdminNav: NavItemDef[] = [
    {
        title: "Management",
        url: "/location-admin",
        icon: Building2,
        exact: true,
    },
    {
        title: "Dashboard",
        url: "/dashboard",
        icon: Home,
    },
    {
        title: "Calls",
        url: "/calls",
        icon: Phone,
    },
    {
        title: "Callback Queue",
        url: "/callbacks",
        icon: PhoneForwarded,
    },
    {
        title: "Call Statuses",
        url: "/institution-admin/call-statuses",
        icon: Tag,
    },
]

const staffNav: NavItemDef[] = [
    {
        title: "Dashboard",
        url: "/dashboard",
        icon: Home,
    },
    {
        title: "Calls",
        url: "/calls",
        icon: Phone,
    },
    {
        title: "Callback Queue",
        url: "/callbacks",
        icon: PhoneForwarded,
    },
]

// Group oversight (DSO) — read-only cross-practice dashboard.
const groupNav: NavItemDef[] = [
    {
        title: "Group Dashboard",
        url: "/group",
        icon: Layers,
        exact: true,
    },
]

// Institution setup nav items
const navSetup: NavItemDef[] = [
    {
        title: "Setup Overview",
        url: "/setup",
        icon: ClipboardList,
        exact: true,
    },
    {
        title: "Appointment Types",
        url: "/setup/appointment-types",
        icon: CalendarCheck,
    },
    {
        title: "Providers & Scheduling",
        url: "/setup/providers",
        icon: UserCog,
    },
    {
        title: "Operatories",
        url: "/setup/operatories",
        icon: Armchair,
    },
    {
        title: "Insurance Plans",
        url: "/setup/insurance-plans",
        icon: Shield,
    },
    {
        title: "Audit Logs",
        url: "/setup/audit-logs",
        icon: ShieldCheck,
    },
]

function NavItem({ item, isActive }: { item: NavItemDef; isActive: boolean }) {
    return (
        <SidebarMenuItem>
            <SidebarMenuButton
                asChild
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
                    <item.icon className={`transition-colors ${isActive ? "text-sidebar-primary" : ""}`} />
                    <span>{item.title}</span>
                </Link>
            </SidebarMenuButton>
        </SidebarMenuItem>
    )
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
    const { user } = useAuth();
    const { hasPms } = useInstitution();
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
    const setupNav = user?.role === "STAFF"
        ? navSetup.filter((item) => item.url !== "/setup" && item.url !== "/setup/audit-logs")
        : navSetup;

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
                            {/* No-PMS tenants are call-intelligence-only: surface the
                                patient directory in place of Practice Setup. */}
                            {isInstitution && !hasPms && (
                                <NavItem
                                    item={{ title: "Patients", url: "/patients", icon: Users }}
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
                                            icon: Mail,
                                        }}
                                        isActive={location.pathname === "/institution-admin/email-templates" || location.pathname.startsWith("/institution-admin/email-templates")}
                                    />
                                )}
                                <NavItem
                                    item={{
                                        title: "Email Preferences",
                                        url: "/notification-preferences",
                                        icon: MailCheck,
                                    }}
                                    isActive={location.pathname === "/notification-preferences"}
                                />
                                {user?.role === "INSTITUTION_ADMIN" && (
                                    <NavItem
                                        item={{
                                            title: "Settings",
                                            url: "/institution-admin/settings",
                                            icon: Settings,
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
