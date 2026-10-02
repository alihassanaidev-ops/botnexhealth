import { Outlet, Navigate, useLocation } from "react-router-dom";
import { SidebarProvider } from "@/components/ui/sidebar"
import { AppSidebar } from "@/components/app-sidebar"
import { TopNav } from "@/components/TopNav"
import BrandLoader from "@/components/BrandLoader"
import { useAuth } from "@/context/AuthContext"
import { useSelectedLocationId } from "@/context/LocationContext"

export default function DashboardWrapper() {
    const { user, isLoading } = useAuth();
    const selectedLocationId = useSelectedLocationId();
    const location = useLocation();

    if (isLoading) {
        return <BrandLoader fullScreen />;
    }

    if (!user) {
        return <Navigate to="/login" replace state={{ from: location }} />;
    }

    return (
        <SidebarProvider className="flex-col">
            <TopNav />
            <div className="flex min-h-0 w-full flex-1">
                <AppSidebar />
                <main className="w-full">
                    <div className="p-4">
                        <Outlet key={selectedLocationId ?? "no-location"} />
                    </div>
                </main>
            </div>
        </SidebarProvider>
    )
}
