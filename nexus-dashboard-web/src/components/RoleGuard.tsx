import { Navigate, useLocation } from "react-router-dom"
import { useAuth } from "@/context/AuthContext"
import type { User } from "@/types"

interface RoleGuardProps {
    allowed: User["role"][]
    children: React.ReactNode
}

export default function RoleGuard({ allowed, children }: RoleGuardProps) {
    const { user } = useAuth()
    const location = useLocation()

    if (!user) return <Navigate to="/login" replace state={{ from: location }} />

    if (!allowed.includes(user.role)) {
        const home = user.role === "SUPER_ADMIN"
            ? "/admin"
            : user.role === "INSTITUTION_ADMIN"
                ? "/institution-admin"
                : user.role === "LOCATION_ADMIN"
                    ? "/location-admin"
                    : "/dashboard"
        return <Navigate to={home} replace />
    }

    return <>{children}</>
}
