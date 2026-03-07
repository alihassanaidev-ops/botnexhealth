import { Navigate } from "react-router-dom"
import { useAuth } from "@/context/AuthContext"

export default function RoleRedirect() {
    const { user } = useAuth()

    if (!user) return null

    if (user.role === "SUPER_ADMIN") {
        return <Navigate to="/admin" replace />
    }

    if (user.role === "INSTITUTION_ADMIN") {
        return <Navigate to="/institution-admin" replace />
    }

    return <Navigate to="/dashboard" replace />
}
