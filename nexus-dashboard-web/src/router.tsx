import { createBrowserRouter } from "react-router-dom";
import DashboardWrapper from "./components/DashboardWrapper";
import Dashboard from "./pages/Dashboard";
import Tenants from "./pages/Tenants";
import Login from "./pages/Login";
import SetPassword from "./pages/SetPassword";
import AppLayout from "./components/AppLayout";

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
                        path: "tenants",
                        element: <Tenants />,
                    },
                ],
            },
        ]
    }
]);
