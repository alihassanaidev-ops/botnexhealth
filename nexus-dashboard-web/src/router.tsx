/* eslint-disable react-refresh/only-export-components */
import { lazy, Suspense } from "react";
import { createBrowserRouter } from "react-router-dom";
import DashboardWrapper from "./components/DashboardWrapper";
import RoleRedirect from "./components/RoleRedirect";
import RoleGuard from "./components/RoleGuard";
import PmsGuard from "./components/PmsGuard";
import AppLayout from "./components/AppLayout";
import RouteError from "./components/RouteError";
import { PageSkeleton } from "@/components/ui/skeletons";

// Auth pages — eagerly loaded (small, needed immediately)
import Login from "./pages/Login";
import SetPassword from "./pages/SetPassword";

// All other pages — lazy loaded
const AdminDashboard = lazy(() => import("./pages/AdminDashboard"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const SetupOverview = lazy(() => import("./pages/SetupOverview"));
const Institutions = lazy(() => import("./pages/Tenants"));
const InstitutionDetailPage = lazy(() => import("./pages/TenantDetail"));
const AppointmentTypes = lazy(() => import("./pages/AppointmentTypes"));
const ProvidersScheduling = lazy(() => import("./pages/ProvidersScheduling"));
const Operatories = lazy(() => import("./pages/Operatories"));
const Calls = lazy(() => import("./pages/Calls"));
const Callbacks = lazy(() => import("./pages/Callbacks"));
const AuditLogs = lazy(() => import("./pages/AuditLogs"));
const AdminAuditLogs = lazy(() => import("./pages/AdminAuditLogs"));
const AdminUserManagement = lazy(() => import("./pages/AdminUserManagement"));
const TwilioPhoneNumbers = lazy(() => import("./pages/TwilioPhoneNumbers"));
const InstitutionAdminPanel = lazy(() => import("./pages/InstitutionAdminPanel"));
const LocationAdminPanel = lazy(() => import("./pages/LocationAdminPanel"));
const InstitutionUserManagement = lazy(() => import("./pages/InstitutionUserManagement"));
const InstitutionSettings = lazy(() => import("./pages/InstitutionSettings"));
const WorkflowStatuses = lazy(() => import("./pages/WorkflowStatuses"));
const InsurancePlans = lazy(() => import("./pages/InsurancePlans"));
const EmailTemplates = lazy(() => import("./pages/EmailTemplates"));
const NotificationPreferences = lazy(() => import("./pages/NotificationPreferences"));
const Security = lazy(() => import("./pages/Security"));
const Patients = lazy(() => import("./pages/Patients"));
const GroupDashboard = lazy(() => import("./pages/GroupDashboard"));
const Groups = lazy(() => import("./pages/Groups"));

function LazyFallback() {
    return <PageSkeleton />;
}

function S({ children }: { children: React.ReactNode }) {
    return <Suspense fallback={<LazyFallback />}>{children}</Suspense>;
}

export const router = createBrowserRouter([
    {
        element: <AppLayout />,
        errorElement: <RouteError />,
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
                errorElement: <RouteError />,
                children: [
                    {
                        path: "/",
                        element: <RoleRedirect />,
                    },
                    {
                        path: "admin",
                        element: (
                            <RoleGuard allowed={["SUPER_ADMIN"]}>
                                <S><AdminDashboard /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "dashboard",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <S><Dashboard /></S>
                            </RoleGuard>
                        ),
                    },

                    {
                        path: "institution-admin",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN"]}>
                                <S><InstitutionAdminPanel /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "institution-admin/users",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN"]}>
                                <S><InstitutionUserManagement /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "institution-admin/settings",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN"]}>
                                <S><InstitutionSettings /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "institution-admin/email-templates",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN"]}>
                                <S><EmailTemplates /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "notification-preferences",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <S><NotificationPreferences /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        // Personal security settings. Available to every
                        // signed-in role since the operations are all
                        // scoped to the user's own factors.
                        path: "security",
                        element: (
                            <RoleGuard allowed={["SUPER_ADMIN", "INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <S><Security /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "location-admin",
                        element: (
                            <RoleGuard allowed={["LOCATION_ADMIN"]}>
                                <S><LocationAdminPanel /></S>
                            </RoleGuard>
                        ),
                    },

                    {
                        path: "institutions",
                        element: (
                            <RoleGuard allowed={["SUPER_ADMIN"]}>
                                <S><Institutions /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "institutions/:slug",
                        element: (
                            <RoleGuard allowed={["SUPER_ADMIN"]}>
                                <S><InstitutionDetailPage /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "admin/twilio",
                        element: (
                            <RoleGuard allowed={["SUPER_ADMIN"]}>
                                <S><TwilioPhoneNumbers /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "admin/audit-logs",
                        element: (
                            <RoleGuard allowed={["SUPER_ADMIN"]}>
                                <S><AdminAuditLogs /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "admin/users",
                        element: (
                            <RoleGuard allowed={["SUPER_ADMIN"]}>
                                <S><AdminUserManagement /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN"]}>
                                <PmsGuard><S><SetupOverview /></S></PmsGuard>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/appointment-types",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <PmsGuard><S><AppointmentTypes /></S></PmsGuard>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/providers",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <PmsGuard><S><ProvidersScheduling /></S></PmsGuard>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/operatories",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <PmsGuard><S><Operatories /></S></PmsGuard>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/insurance-plans",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <PmsGuard><S><InsurancePlans /></S></PmsGuard>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "setup/audit-logs",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN"]}>
                                <S><AuditLogs /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "calls",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <S><Calls /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "callbacks",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <S><Callbacks /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "institution-admin/call-statuses",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN"]}>
                                <S><WorkflowStatuses /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "patients",
                        element: (
                            <RoleGuard allowed={["INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"]}>
                                <S><Patients /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "group",
                        element: (
                            <RoleGuard allowed={["GROUP_ADMIN"]}>
                                <S><GroupDashboard /></S>
                            </RoleGuard>
                        ),
                    },
                    {
                        path: "groups",
                        element: (
                            <RoleGuard allowed={["SUPER_ADMIN"]}>
                                <S><Groups /></S>
                            </RoleGuard>
                        ),
                    },
                ],
            },
        ]
    }
]);
