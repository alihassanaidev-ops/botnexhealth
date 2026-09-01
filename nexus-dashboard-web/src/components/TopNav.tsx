import { Link } from "react-router-dom"
import { Bell, ChevronDown, LogOut, Shield, Moon, Sun } from "lucide-react"
import { useTheme } from "next-themes"
import { AppShellToggle } from "@/components/foundation/AppShell"
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
        <header className="shell-topbar">
            <AppShellToggle />
            <Link to="/" className="shell-brand">
                <img src="/scalenexuslogo.svg" alt="ScaleNexus" />
                <span>ScaleNexus.AI</span>
            </Link>

            <div className="shell-topbar-spacer" />

            <div className="shell-topbar-actions">
                {isInstitution && (
                    <button
                        type="button"
                        onClick={() => setIsDialogOpen(true)}
                        aria-label="Notifications"
                        className="shell-icon-button shell-notification-button"
                    >
                        <Bell className={unreadCount > 0 ? "animate-bell-swing" : ""} />
                        {unreadCount > 0 && (
                            <span className="shell-notification-count">
                                {unreadCount > 9 ? "9+" : unreadCount}
                            </span>
                        )}
                    </button>
                )}

                <details className="shell-profile">
                    <summary className="shell-profile-trigger">
                        <span className="shell-avatar">{initials}</span>
                        <span className="shell-profile-email">{displayEmail}</span>
                        <ChevronDown className="shell-profile-chevron" aria-hidden="true" />
                    </summary>
                    <div className="shell-profile-menu">
                        <div className="shell-profile-meta">
                            <span className="shell-avatar">{initials}</span>
                            <span className="shell-profile-meta-copy">
                                <strong>{displayEmail}</strong>
                                <span>{formatRoleLabel(user?.role)}</span>
                            </span>
                        </div>
                        <div className="shell-profile-separator" />
                        <Link to="/security" className="shell-profile-action">
                            <Shield />
                            Security
                        </Link>
                        <button
                            type="button"
                            className="shell-profile-action"
                            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
                        >
                            {theme === "dark" ? <Sun /> : <Moon />}
                            {theme === "dark" ? "Light mode" : "Dark mode"}
                        </button>
                        <div className="shell-profile-separator" />
                        <button
                            type="button"
                            className="shell-profile-action shell-profile-action-danger"
                            onClick={() => signOut()}
                        >
                            <LogOut />
                            Log out
                        </button>
                    </div>
                </details>
            </div>
        </header>
    )
}
