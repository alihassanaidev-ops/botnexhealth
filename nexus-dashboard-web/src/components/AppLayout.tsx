import { Outlet } from "react-router-dom";
import { AuthProvider } from "@/context/AuthContext";
import { Toaster } from "@/components/ui/sonner";

export default function AppLayout() {
    return (
        <AuthProvider>
            <Outlet />
            <Toaster />
        </AuthProvider>
    );
}
