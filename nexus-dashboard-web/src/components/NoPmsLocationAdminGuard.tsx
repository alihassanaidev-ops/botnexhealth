import { Navigate } from "react-router-dom"
import { useAuth } from "@/context/AuthContext"
import { useInstitution } from "@/context/InstitutionContext"

interface NoPmsLocationAdminGuardProps {
    children: React.ReactNode
    redirectTo?: string
}

export default function NoPmsLocationAdminGuard({
    children,
    redirectTo = "/dashboard",
}: NoPmsLocationAdminGuardProps) {
    const { user } = useAuth()
    const { hasPms, isLoading } = useInstitution()

    if (!isLoading && user?.role === "LOCATION_ADMIN" && !hasPms) {
        return <Navigate to={redirectTo} replace />
    }

    return <>{children}</>
}
