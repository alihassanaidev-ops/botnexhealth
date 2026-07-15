import { Outlet, Navigate, useLocation } from "react-router-dom";
import { SidebarProvider } from "@/components/ui/sidebar"
import { AppSidebar } from "@/components/app-sidebar"
import { TopNav } from "@/components/TopNav"
import { PageSkeleton } from "@/components/ui/skeletons"
import { useAuth } from "@/context/AuthContext"

export default function DashboardWrapper() {
    const { user, isLoading } = useAuth();
    const location = useLocation();

    if (isLoading) {
        return <PageSkeleton />;
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
                        <Outlet />
                    </div>
                </main>
            </div>
        </SidebarProvider>
    )
}
