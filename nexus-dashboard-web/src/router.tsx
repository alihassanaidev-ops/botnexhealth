import { createBrowserRouter } from "react-router-dom";
import DashboardWrapper from "./components/DashboardWrapper";
import RoleRedirect from "./components/RoleRedirect";
import RoleGuard from "./components/RoleGuard";
import AdminDashboard from "./pages/AdminDashboard";
import Dashboard from "./pages/Dashboard";
import Tenants from "./pages/Tenants";
import TenantDetail from "./pages/TenantDetail";
import Login from "./pages/Login";
import SetPassword from "./pages/SetPassword";
import AppLayout from "./components/AppLayout";
import AppointmentTypes from "./pages/AppointmentTypes";
import ProvidersScheduling from "./pages/ProvidersScheduling";
import Operatories from "./pages/Operatories";


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
                            <RoleGuard allowed={["TENANT"]}>
                                <Dashboard />
                            </RoleGuard>
                        ),
                    },

                    {
                        path: "tenants",
                        element: (
                            <RoleGuard allowed={["ADMIN"]}>
                                <Tenants />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "tenants/:slug",
                        element: (
                            <RoleGuard allowed={["ADMIN"]}>
                                <TenantDetail />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/appointment-types",
                        element: (
                            <RoleGuard allowed={["TENANT"]}>
                                <AppointmentTypes />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/providers",
                        element: (
                            <RoleGuard allowed={["TENANT"]}>
                                <ProvidersScheduling />
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/operatories",
                        element: (
                            <RoleGuard allowed={["TENANT"]}>
                                <Operatories />
                            </RoleGuard>
                        ),
                    },
                ],
            },
        ]
    }
]);
