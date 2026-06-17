import { Navigate } from "react-router-dom"
import { useInstitution } from "@/context/InstitutionContext"

interface PmsGuardProps {
    children: React.ReactNode
    /** Where to send no-PMS tenants. Defaults to the dashboard. */
    redirectTo?: string
}

/**
 * Blocks Practice Setup routes for call-intelligence-only (no-PMS) tenants.
 * Belt to the sidebar's suspenders: even if a no-PMS user deep-links to a
 * /setup route, they're redirected to their dashboard. While the institution
 * profile is still loading, hasPms defaults to true so PMS tenants never
 * flicker out of their own setup pages.
 */
export default function PmsGuard({ children, redirectTo = "/dashboard" }: PmsGuardProps) {
    const { hasPms, isLoading } = useInstitution()

    if (!isLoading && !hasPms) {
        return <Navigate to={redirectTo} replace />
    }
    return <>{children}</>
}
