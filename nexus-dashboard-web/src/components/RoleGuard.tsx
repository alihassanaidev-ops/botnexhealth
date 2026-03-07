import { Navigate } from "react-router-dom"
import { useAuth } from "@/context/AuthContext"
import type { User } from "@/types"

interface RoleGuardProps {
    allowed: User["role"][]
    children: React.ReactNode
}

export default function RoleGuard({ allowed, children }: RoleGuardProps) {
    const { user } = useAuth()

    if (!user) return null

    if (!allowed.includes(user.role)) {
        const home = user.role === "SUPER_ADMIN" ? "/admin" : "/dashboard"
        return <Navigate to={home} replace />
    }

    return <>{children}</>
}
