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
import CustomFields from "./pages/CustomFields";
import AuditLogs from "./pages/AuditLogs"
import AdminAuditLogs from "./pages/AdminAuditLogs"
import TwilioPhoneNumbers from "./pages/TwilioPhoneNumbers";


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
                            <RoleGuard allowed={["ADMIN"]}>
                                <AdminDashboard />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "dashboard",
                        element: (
                            <RoleGuard allowed={["INSTITUTION", "LOCATION"]}>
                                <Dashboard />
                            </RoleGuard>
                        ),
                    },

                    {
                        path: "institutions",
                        element: (
                            <RoleGuard allowed={["ADMIN"]}>
                                <Institutions />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "institutions/:slug",
                        element: (
                            <RoleGuard allowed={["ADMIN"]}>
                                <InstitutionDetailPage />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "admin/twilio",
                        element: (
                            <RoleGuard allowed={["ADMIN"]}>
                                <TwilioPhoneNumbers />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "admin/audit-logs",
                        element: (
                            <RoleGuard allowed={["ADMIN"]}>
                                <AdminAuditLogs />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/appointment-types",
                        element: (
                            <RoleGuard allowed={["INSTITUTION", "LOCATION"]}>
                                <AppointmentTypes />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/providers",
                        element: (
                            <RoleGuard allowed={["INSTITUTION", "LOCATION"]}>
                                <ProvidersScheduling />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/operatories",
                        element: (
                            <RoleGuard allowed={["INSTITUTION", "LOCATION"]}>
                                <Operatories />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/custom-fields",
                        element: (
                            <RoleGuard allowed={["INSTITUTION", "LOCATION"]}>
                                <CustomFields />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/audit-logs",
                        element: (
                            <RoleGuard allowed={["INSTITUTION", "LOCATION"]}>
                                <AuditLogs />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "calls",
                        element: (
                            <RoleGuard allowed={["INSTITUTION", "LOCATION"]}>
                                <Calls />
                            </RoleGuard>
                        ),
                    },
                ],
            },
        ]
    }
]);
