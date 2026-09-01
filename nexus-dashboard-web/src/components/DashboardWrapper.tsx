import { Outlet, Navigate, useLocation } from "react-router-dom";
import { AppSidebar } from "@/components/app-sidebar"
import { TopNav } from "@/components/TopNav"
import { AppShellBody, AppShellProvider } from "@/components/foundation/AppShell"
import BrandLoader from "@/components/BrandLoader"
import { useAuth } from "@/context/AuthContext"

export default function DashboardWrapper() {
    const { user, isLoading } = useAuth();
    const location = useLocation();

    if (isLoading) {
        return <BrandLoader fullScreen />;
    }

    if (!user) {
        return <Navigate to="/login" replace state={{ from: location }} />;
    }

    return (
        <AppShellProvider>
            <TopNav />
            <AppShellBody sidebar={<AppSidebar />}>
                <Outlet />
            </AppShellBody>
        </AppShellProvider>
    )
}
