import {
    Sidebar,
    SidebarContent,
    SidebarFooter,
    SidebarGroup,
    SidebarGroupContent,
    SidebarGroupLabel,
    SidebarHeader,
    SidebarMenu,
    SidebarMenuButton,
    SidebarMenuItem,
    SidebarRail,
} from "@/components/ui/sidebar"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
    Home,
    Users,
    Building2,
    ChevronUp,
    LogOut,
    CalendarCheck,
    UserCog,
    Armchair,
    LayoutDashboard,
    Phone,
    PhoneForwarded,
    Shield,
    ShieldCheck,
    MessageSquare,
    Moon,
    Sun,
    Bell
} from "lucide-react"
import { Link, useLocation } from "react-router-dom"
import { useTheme } from "next-themes"
import { useAuth } from "@/context/AuthContext"
import { useNotifications } from "@/context/NotificationContext"
import { formatRoleLabel} from "@/lib/utils"

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
]

const locationAdminNav: NavItemDef[] = [
    {
        title: "Location Admin",
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

// Institution setup nav items
const navSetup: NavItemDef[] = [
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
                        : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"
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
    const { user, signOut } = useAuth();
    const { unreadCount, setIsDialogOpen } = useNotifications();
    const location = useLocation();
    const { theme, setTheme } = useTheme();

    const displayEmail = user?.email ?? "—";
    const initials = (user?.email ?? "?").slice(0, 2).toUpperCase();
    const isAdmin = user?.role === "SUPER_ADMIN";
    const isInstitution =
        user?.role === "INSTITUTION_ADMIN" ||
        user?.role === "LOCATION_ADMIN" ||
        user?.role === "STAFF";
    const mainNav = isAdmin
        ? adminNav
        : user?.role === "INSTITUTION_ADMIN"
            ? institutionAdminNav
            : user?.role === "LOCATION_ADMIN"
                ? locationAdminNav
                : staffNav;
    const setupNav = user?.role === "STAFF"
        ? navSetup.filter((item) => item.url !== "/setup/audit-logs")
        : navSetup;

    return (
        <Sidebar collapsible="icon" {...props}>
            <SidebarHeader>
                <SidebarMenu>
                    <SidebarMenuItem>
                        <SidebarMenuButton size="lg" asChild>
                            <Link to="/">
                                <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-gradient-to-br from-violet-600 to-purple-700 text-white shadow-sm shadow-purple-900/30">
                                    <span className="text-sm font-bold tracking-tight">N</span>
                                </div>
                                <div className="flex flex-col gap-0.5 leading-none">
                                    <span className="font-semibold text-sidebar-foreground">Nexus Dental</span>
                                    <span className="text-xs text-sidebar-foreground/50">Dashboard</span>
                                </div>
                            </Link>
                        </SidebarMenuButton>
                    </SidebarMenuItem>
                </SidebarMenu>
            </SidebarHeader>
            <SidebarContent>
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
                        </SidebarMenu>
                    </SidebarGroupContent>
                </SidebarGroup>
                {isInstitution && (
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
                                        isActive={location.pathname === item.url || location.pathname.startsWith(item.url + "/")}
                                    />
                                ))}
                            </SidebarMenu>
                        </SidebarGroupContent>
                    </SidebarGroup>
                )}
                {/* Notifications - Last item in sidebar */}
                {isInstitution && (
                    <SidebarGroup className="mt-auto">
                        <SidebarGroupContent>
                            <SidebarMenu>
                                <SidebarMenuItem>
                                    <SidebarMenuButton
                                        onClick={() => setIsDialogOpen(true)}
                                        tooltip="Notifications"
                                        className="relative transition-all duration-150 rounded-md text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground cursor-pointer"
                                    >
                                        <div className="relative">
                                            <Bell className={`h-5 w-5 ${unreadCount > 0 ? 'animate-bell-swing' : ''}`} />
                                            {unreadCount > 0 && (
                                                <span className="absolute -top-0.5 -right-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-red-500 text-[10px] font-bold text-white">
                                                    {unreadCount > 9 ? "9+" : unreadCount}
                                                </span>
                                            )}
                                        </div>
                                        <span>Notifications</span>
                                    </SidebarMenuButton>
                                </SidebarMenuItem>
                            </SidebarMenu>
                        </SidebarGroupContent>
                    </SidebarGroup>
                )}
            </SidebarContent>
            <SidebarFooter>
                <SidebarMenu>
                    <SidebarMenuItem>
                        <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                                <SidebarMenuButton
                                    size="lg"
                                    className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground hover:bg-sidebar-accent/50 transition-colors duration-150"
                                >
                                    <div className="h-7 w-7 rounded-md bg-gradient-to-br from-violet-600 to-purple-700 flex items-center justify-center text-white shadow-sm shadow-purple-900/30 shrink-0">
                                        <span className="text-[11px] font-bold">{initials}</span>
                                    </div>
                                    <div className="grid flex-1 text-left text-sm leading-tight">
                                        <span className="truncate font-medium text-sidebar-foreground">{displayEmail}</span>
                                        <span className="text-[10px] text-sidebar-foreground/40">{formatRoleLabel(user?.role)}</span>
                                    </div>
                                    <ChevronUp className="ml-auto h-4 w-4 text-sidebar-foreground/40" />
                                </SidebarMenuButton>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent
                                className="w-[--radix-dropdown-menu-trigger-width] min-w-56 rounded-lg"
                                side="top"
                                align="end"
                                sideOffset={4}
                            >
                                <DropdownMenuLabel className="p-0 font-normal">
                                    <div className="flex items-center gap-2 px-1 py-1.5 text-left text-sm">
                                        <div className="h-8 w-8 rounded-md bg-gradient-to-br from-violet-600 to-purple-700 flex items-center justify-center text-white">
                                            <span className="text-xs font-bold">{initials}</span>
                                        </div>
                                        <div className="grid flex-1 text-left text-sm leading-tight">
                                            <span className="truncate font-semibold">{displayEmail}</span>
                                            <span className="text-[10px] text-muted-foreground">{formatRoleLabel(user?.role)}</span>
                                        </div>
                                    </div>
                                </DropdownMenuLabel>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem
                                    onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
                                    className="gap-2"
                                >
                                    {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                                    {theme === "dark" ? "Light mode" : "Dark mode"}
                                </DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem onClick={() => signOut()} className="gap-2 text-destructive focus:text-destructive">
                                    <LogOut className="h-4 w-4" />
                                    Log out
                                </DropdownMenuItem>
                            </DropdownMenuContent>
                        </DropdownMenu>
                    </SidebarMenuItem>
                </SidebarMenu>
            </SidebarFooter>
            <SidebarRail />
        </Sidebar>
    )
}
