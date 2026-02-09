import { useRef } from 'react';
import { Outlet } from "react-router-dom";
import { AuthProvider } from "@/context/AuthContext";
import { Toaster } from "@/components/ui/sonner";

export default function AppLayout() {
    // Store the initial hash so it's available even if cleared later
    const initialHash = useRef(window.location.hash);

    return (
        <AuthProvider>
            <Outlet context={{ initialHash: initialHash.current }} />
            <Toaster />
        </AuthProvider>
    );
}
