import { createBrowserRouter } from "react-router-dom";
import DashboardWrapper from "./components/DashboardWrapper";
import RoleRedirect from "./components/RoleRedirect";
import RoleGuard from "./components/RoleGuard";
import AdminDashboard from "./pages/AdminDashboard";
import Dashboard from "./pages/Dashboard";
import Institutions from "./pages/Tenants";
import InstitutionDetailPage from "./pages/TenantDetail";
import Login from "./pages/Login";
import SetPassword from "./pages/SetPassword";
import AppLayout from "./components/AppLayout";
import AppointmentTypes from "./pages/AppointmentTypes";
import ProvidersScheduling from "./pages/ProvidersScheduling";
import Operatories from "./pages/Operatories";
import Calls from "./pages/Calls";
import AuditLogs from "./pages/AuditLogs"
import AdminAuditLogs from "./pages/AdminAuditLogs"
import TwilioPhoneNumbers from "./pages/TwilioPhoneNumbers";
import InstitutionAdminPanel from "./pages/InstitutionAdminPanel";
import LocationAdminPanel from "./pages/LocationAdminPanel";
import InstitutionUserManagement from "./pages/InstitutionUserManagement";


export const router = createBrowserRouter([
    {
        element: <AppLayout />,
        children: [
            {
                path: "/login",
                element: <Login />,
            },
            {
                path: "/set-password",
                element: <SetPassword />,
            },
            {
                path: "/",
                element: <DashboardWrapper />,
                children: [
                    {
                        path: "/",
                        element: <RoleRedirect />,
                    },
                    {
                        path: "admin",
                        element: (
                            <RoleGuard allowed={["SUPER_ADMIN"]}>
                                <AdminDashboard />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "dashboard",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <Dashboard />
                            </RoleGuard>
                        ),
                    },

                    {
                        path: "institution-admin",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN"]}>
                                <InstitutionAdminPanel />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "institution-admin/users",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN"]}>
                                <InstitutionUserManagement />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "location-admin",
                        element: (
                            <RoleGuard allowed={["LOCATION_ADMIN"]}>
                                <LocationAdminPanel />
                            </RoleGuard>
                        ),
                    },

                    {
                        path: "institutions",
                        element: (
                            <RoleGuard allowed={["SUPER_ADMIN"]}>
                                <Institutions />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "institutions/:slug",
                        element: (
                            <RoleGuard allowed={["SUPER_ADMIN"]}>
                                <InstitutionDetailPage />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "admin/twilio",
                        element: (
                            <RoleGuard allowed={["SUPER_ADMIN"]}>
                                <TwilioPhoneNumbers />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "admin/audit-logs",
                        element: (
                            <RoleGuard allowed={["SUPER_ADMIN"]}>
                                <AdminAuditLogs />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/appointment-types",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <AppointmentTypes />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/providers",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <ProvidersScheduling />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/operatories",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <Operatories />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/audit-logs",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN"]}>
                                <AuditLogs />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "calls",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <Calls />
                            </RoleGuard>
                        ),
                    },
                ],
            },
        ]
    }
]);
