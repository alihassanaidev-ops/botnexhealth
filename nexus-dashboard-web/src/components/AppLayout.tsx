import { Outlet } from "react-router-dom";
import { AuthProvider } from "@/context/AuthContext";
import { NotificationProvider } from "@/context/NotificationContext";
import { Toaster } from "@/components/ui/sonner";
import { NotificationDialog } from "@/components/ui/notification-dialog";

export default function AppLayout() {
    return (
        <AuthProvider>
            <NotificationProvider>
                <Outlet />
                <Toaster />
                <NotificationDialog />
            </NotificationProvider>
        </AuthProvider>
    );
}
