import {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useMemo,
    useState,
    type ReactNode,
} from "react"
import { Menu, PanelLeftClose, PanelLeftOpen } from "lucide-react"

import { useIsMobile } from "@/hooks/use-mobile"
import "./app-shell.css"

/* eslint-disable react-refresh/only-export-components */

type AppShellContextValue = {
    collapsed: boolean
    mobileOpen: boolean
    isMobile: boolean
    closeMobile: () => void
    toggle: () => void
}

const AppShellContext = createContext<AppShellContextValue | null>(null)

export function AppShellProvider({ children }: { children: ReactNode }) {
    const isMobile = useIsMobile()
    const [collapsed, setCollapsed] = useState(false)
    const [mobileOpen, setMobileOpen] = useState(false)

    const toggle = useCallback(() => {
        if (isMobile) {
            setMobileOpen((open) => !open)
        } else {
            setCollapsed((value) => !value)
        }
    }, [isMobile])

    const closeMobile = useCallback(() => setMobileOpen(false), [])

    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "b") {
                event.preventDefault()
                toggle()
            }
            if (event.key === "Escape") closeMobile()
        }
        window.addEventListener("keydown", onKeyDown)
        return () => window.removeEventListener("keydown", onKeyDown)
    }, [closeMobile, toggle])

    const visibleMobileOpen = isMobile && mobileOpen

    const value = useMemo(
        () => ({ collapsed, mobileOpen: visibleMobileOpen, isMobile, closeMobile, toggle }),
        [collapsed, visibleMobileOpen, isMobile, closeMobile, toggle],
    )

    return (
        <AppShellContext.Provider value={value}>
            <div
                className="app-shell"
                data-collapsed={collapsed ? "true" : "false"}
                data-mobile-open={visibleMobileOpen ? "true" : "false"}
            >
                {children}
            </div>
        </AppShellContext.Provider>
    )
}

export function useAppShell() {
    const context = useContext(AppShellContext)
    if (!context) throw new Error("useAppShell must be used inside AppShellProvider")
    return context
}

export function AppShellToggle() {
    const { collapsed, isMobile, toggle } = useAppShell()
    const Icon = isMobile ? Menu : collapsed ? PanelLeftOpen : PanelLeftClose

    return (
        <button type="button" className="app-shell-toggle" onClick={toggle} aria-label="Toggle sidebar">
            <Icon aria-hidden="true" />
        </button>
    )
}

export function AppShellBody({ sidebar, children }: { sidebar: ReactNode; children: ReactNode }) {
    const { mobileOpen, closeMobile } = useAppShell()

    return (
        <div className="app-shell-body">
            <button
                type="button"
                aria-label="Close sidebar"
                className="app-shell-backdrop"
                data-visible={mobileOpen ? "true" : "false"}
                onClick={closeMobile}
            />
            {sidebar}
            <main className="app-shell-main">
                <div className="app-shell-content">{children}</div>
            </main>
        </div>
    )
}
