/**
 * Holds the active institution's profile for institution-scoped users.
 *
 * Why this exists: the dashboard needs the tenant's PMS mode (`has_pms`) to
 * decide whether to show the Practice Setup nav/routes (PMS tenants) or the
 * call-intelligence Patients view (no-PMS tenants). The mode lives on the
 * institution and is served by `GET /institution/me`. SUPER_ADMIN and
 * unauthenticated users have no institution; the provider returns
 * `hasPms = true` (the PMS default) for them so platform-level views are
 * unaffected.
 */

import {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useMemo,
    useState,
    type ReactNode,
} from "react"

import { getInstitutionPortalMe, type InstitutionPortalMe } from "@/lib/institution-portal-api"
import { useAuth } from "@/context/AuthContext"

interface InstitutionContextValue {
    profile: InstitutionPortalMe | null
    /** False only for confirmed call-intelligence-only tenants. */
    hasPms: boolean
    pmsType: string | null
    isLoading: boolean
}

const InstitutionContext = createContext<InstitutionContextValue | undefined>(undefined)

const INSTITUTION_SCOPED_ROLES = new Set([
    "INSTITUTION_ADMIN",
    "LOCATION_ADMIN",
    "STAFF",
])

export function InstitutionProvider({ children }: { children: ReactNode }) {
    const { user } = useAuth()
    const [rawProfile, setRawProfile] = useState<InstitutionPortalMe | null>(null)
    const [isLoading, setIsLoading] = useState(false)

    const scoped = !!user && INSTITUTION_SCOPED_ROLES.has(user.role)

    const loadProfile = useCallback(async () => {
        setIsLoading(true)
        try {
            setRawProfile(await getInstitutionPortalMe())
        } catch {
            setRawProfile(null)
        } finally {
            setIsLoading(false)
        }
    }, [])

    useEffect(() => {
        // Only fetch for institution-scoped users. Non-scoped users (SUPER_ADMIN,
        // logged out) are handled by deriving a null profile below.
        if (scoped) {
            void loadProfile()
        }
    }, [scoped, user?.institution_id, loadProfile])

    const value = useMemo<InstitutionContextValue>(() => {
        // Ignore any stale profile once the user is no longer institution-scoped.
        const profile = scoped ? rawProfile : null
        // Default to has-PMS until we know otherwise, so nothing flickers
        // hidden for PMS tenants while the profile loads.
        const hasPms = profile?.has_pms ?? true
        return {
            profile,
            hasPms,
            pmsType: profile?.pms_type ?? null,
            isLoading,
        }
    }, [scoped, rawProfile, isLoading])

    return (
        <InstitutionContext.Provider value={value}>
            {children}
        </InstitutionContext.Provider>
    )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useInstitution() {
    const ctx = useContext(InstitutionContext)
    if (ctx === undefined) {
        throw new Error("useInstitution must be used within an InstitutionProvider")
    }
    return ctx
}
