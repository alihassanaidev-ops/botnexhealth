import { Navigate } from "react-router-dom"
import { useInstitution } from "@/context/InstitutionContext"

interface NoPmsGuardProps {
    children: React.ReactNode
    redirectTo?: string
}

export default function NoPmsGuard({ children, redirectTo = "/dashboard" }: NoPmsGuardProps) {
    const { hasPms, isLoading } = useInstitution()

    if (!isLoading && hasPms) {
        return <Navigate to={redirectTo} replace />
    }

    return <>{children}</>
}
