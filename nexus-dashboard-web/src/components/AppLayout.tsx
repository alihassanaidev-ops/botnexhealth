import { Outlet } from "react-router-dom";
import { AuthProvider } from "@/context/AuthContext";
import { NotificationProvider } from "@/context/NotificationContext";
import { LocationProvider } from "@/context/LocationContext";
import { InstitutionProvider } from "@/context/InstitutionContext";
import { Toaster } from "@/components/ui/sonner";
import { NotificationDialog } from "@/components/ui/notification-dialog";

export default function AppLayout() {
    return (
        <AuthProvider>
            <InstitutionProvider>
                <LocationProvider>
                    <NotificationProvider>
                        <Outlet />
                        <Toaster />
                        <NotificationDialog />
                    </NotificationProvider>
                </LocationProvider>
            </InstitutionProvider>
        </AuthProvider>
    );
}
