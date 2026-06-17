import { Link } from "react-router-dom"
import { Bell, ChevronDown, LogOut, Shield, Moon, Sun } from "lucide-react"
import { useTheme } from "next-themes"
import { SidebarTrigger } from "@/components/ui/sidebar"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useAuth } from "@/context/AuthContext"
import { useNotifications } from "@/context/NotificationContext"
import { formatRoleLabel } from "@/lib/utils"

export function TopNav() {
    const { user, signOut } = useAuth()
    const { unreadCount, setIsDialogOpen } = useNotifications()
    const { theme, setTheme } = useTheme()

    const displayEmail = user?.email ?? "—"
    const initials = (user?.email ?? "?").slice(0, 2).toUpperCase()
    const isInstitution =
        user?.role === "INSTITUTION_ADMIN" ||
        user?.role === "LOCATION_ADMIN" ||
        user?.role === "STAFF"

    return (
        <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b border-zinc-800 bg-zinc-950 px-3 text-zinc-100">
            {/* Left: toggle + brand */}
            <div className="flex items-center gap-2">
                <SidebarTrigger className="h-8 w-8 text-white hover:bg-zinc-800 hover:text-white" />
                <Link to="/" className="flex items-center gap-2">
                    <img
                        src="/scalenexuslogo.svg"
                        alt="ScaleNexus"
                        className="size-7 object-contain brightness-0 invert"
                    />
                    <span className="hidden text-sm font-semibold tracking-tight sm:inline">ScaleNexus.AI</span>
                </Link>
            </div>

            {/* Spacer */}
            <div className="flex-1" />

            {/* Right: notifications + profile */}
            <div className="flex items-center gap-1">
                {isInstitution && (
                    <button
                        onClick={() => setIsDialogOpen(true)}
                        aria-label="Notifications"
                        className="relative flex size-9 items-center justify-center rounded-lg text-white transition-colors hover:bg-zinc-800"
                    >
                        <Bell className={`size-5 ${unreadCount > 0 ? "animate-bell-swing" : ""}`} />
                        {unreadCount > 0 && (
                            <span className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
                                {unreadCount > 9 ? "9+" : unreadCount}
                            </span>
                        )}
                    </button>
                )}

                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <button className="flex items-center gap-2 rounded-lg py-1 pl-1 pr-2 text-left transition-colors hover:bg-zinc-800 data-[state=open]:bg-zinc-800">
                            <div className="flex size-7 items-center justify-center rounded-md bg-gradient-to-br from-violet-600 to-purple-700 text-white">
                                <span className="text-[11px] font-bold">{initials}</span>
                            </div>
                            <span className="hidden max-w-[140px] truncate text-sm font-medium text-zinc-200 md:inline">
                                {displayEmail}
                            </span>
                            <ChevronDown className="size-4 text-zinc-300" />
                        </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent className="min-w-56 rounded-lg" align="end" sideOffset={6}>
                        <DropdownMenuLabel className="p-0 font-normal">
                            <div className="flex items-center gap-2 px-1 py-1.5 text-left text-sm">
                                <div className="flex size-8 items-center justify-center rounded-md bg-gradient-to-br from-violet-600 to-purple-700 text-white">
                                    <span className="text-xs font-bold">{initials}</span>
                                </div>
                                <div className="grid flex-1 leading-tight">
                                    <span className="truncate font-semibold">{displayEmail}</span>
                                    <span className="text-[10px] text-muted-foreground">{formatRoleLabel(user?.role)}</span>
                                </div>
                            </div>
                        </DropdownMenuLabel>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem asChild className="gap-2">
                            <Link to="/security">
                                <Shield className="h-4 w-4" />
                                Security
                            </Link>
                        </DropdownMenuItem>
                        <DropdownMenuItem
                            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
                            className="gap-2"
                        >
                            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                            {theme === "dark" ? "Light mode" : "Dark mode"}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                            onClick={() => signOut()}
                            className="gap-2 text-destructive focus:text-destructive"
                        >
                            <LogOut className="h-4 w-4" />
                            Log out
                        </DropdownMenuItem>
                    </DropdownMenuContent>
                </DropdownMenu>
            </div>
        </header>
    )
}
