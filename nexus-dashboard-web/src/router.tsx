import { createBrowserRouter } from "react-router-dom";
import DashboardWrapper from "./components/DashboardWrapper";
import Dashboard from "./pages/Dashboard";
import Tenants from "./pages/Tenants";
import TenantDetail from "./pages/TenantDetail";
import Login from "./pages/Login";
import SetPassword from "./pages/SetPassword";
import AppLayout from "./components/AppLayout";
import AppointmentTypes from "./pages/AppointmentTypes";
import ProvidersScheduling from "./pages/ProvidersScheduling";
import Operatories from "./pages/Operatories";
import Calls from "./pages/Calls";

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
                        element: <Dashboard />,
                    },
                    {
                        path: "dashboard",
                        element: <Dashboard />,
                    },
                    {
                        path: "calls",
                        element: <Calls />,
                    },
                    {
                        path: "tenants",
                        element: <Tenants />,
                    },
                    {
                        path: "tenants/:slug",
                        element: <TenantDetail />,
                    },
                    {
                        path: "setup/appointment-types",
                        element: <AppointmentTypes />,
                    },
                    {
                        path: "setup/providers",
                        element: <ProvidersScheduling />,
                    },
                    {
                        path: "setup/operatories",
                        element: <Operatories />,
                    },
                ],
            },
        ]
    }
]);
