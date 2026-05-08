/**
 * Single source of truth for which location institution-scoped pages
 * operate against.
 *
 * Why this exists: the backend's `_resolve_institution_location` rejects
 * setup calls without `?location_id=` for any institution that has more
 * than one active location (commit ad223bb — preventing cross-clinic
 * writes when an INSTITUTION_ADMIN forgets to scope). Before this
 * context, only `SetupOverview` had a local selector, so every other
 * setup page (`AppointmentTypes`, `ProvidersScheduling`, `Operatories`,
 * the descriptors and sync routes) silently 400'd.
 *
 * Behaviour by role (matches backend RBAC):
 *   - LOCATION_ADMIN / STAFF: backend hard-pins to user.location_id and
 *     403s any other id. Frontend mirrors the single allowed location
 *     and hides the selector.
 *   - INSTITUTION_ADMIN: shown a dropdown of every active location at
 *     this institution. Selection is persisted in localStorage so
 *     navigations and reloads land on the same clinic.
 *   - SUPER_ADMIN / unauthenticated: provider returns null until an
 *     institution-scoped role is in scope.
 *
 * Persistence is HIPAA-safe: a location_id is an organisational UUID,
 * not PHI. localStorage is the same trust boundary as the access token
 * cookie that's already there.
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

import { listLocations } from "@/lib/tenant-api"
import type { LocationInfo } from "@/types"
import { useAuth } from "@/context/AuthContext"

const STORAGE_KEY = "nex.selectedLocationId"

interface LocationContextValue {
    locations: LocationInfo[]
    selectedLocationId: string | null
    selectedLocation: LocationInfo | null
    setSelectedLocationId: (id: string) => void
    isLoading: boolean
    canSwitch: boolean
    refresh: () => Promise<void>
}

const LocationContext = createContext<LocationContextValue | undefined>(undefined)

export function LocationProvider({ children }: { children: ReactNode }) {
    const { user } = useAuth()
    const [locations, setLocations] = useState<LocationInfo[]>([])
    const [selectedLocationId, setSelectedLocationIdState] = useState<string | null>(null)
    const [isLoading, setIsLoading] = useState(false)

    const isInstitutionScoped =
        user?.role === "INSTITUTION_ADMIN" ||
        user?.role === "LOCATION_ADMIN" ||
        user?.role === "STAFF"

    const canSwitch = user?.role === "INSTITUTION_ADMIN"

    const loadLocations = useCallback(async () => {
        if (!isInstitutionScoped) {
            setLocations([])
            setSelectedLocationIdState(null)
            return
        }
        setIsLoading(true)
        try {
            const fetched = await listLocations()
            setLocations(fetched)

            // Pick the canonical "current" location. For non-switchable
            // roles the backend pins to user.location_id anyway; we just
            // mirror that so API calls send the matching id (a noop for
            // the backend, but cleaner than sending nothing).
            const stored = canSwitch ? localStorage.getItem(STORAGE_KEY) : null
            const valid = (id: string | null) =>
                Boolean(id) && fetched.some((l) => l.id === id)
            let next: string | null = null
            if (valid(stored)) {
                next = stored
            } else if (fetched.length > 0) {
                next = fetched[0].id
            }
            setSelectedLocationIdState(next)
            if (canSwitch && next && next !== stored) {
                localStorage.setItem(STORAGE_KEY, next)
            }
        } catch {
            // Listing locations may fail before the user finishes
            // authenticating; surface that as "no selection" and let
            // calling pages handle it. The next refresh() (e.g. after
            // a login redirect) repopulates.
            setLocations([])
            setSelectedLocationIdState(null)
        } finally {
            setIsLoading(false)
        }
    }, [isInstitutionScoped, canSwitch])

    useEffect(() => {
        loadLocations()
    }, [loadLocations])

    const setSelectedLocationId = useCallback(
        (id: string) => {
            setSelectedLocationIdState(id)
            if (canSwitch) {
                localStorage.setItem(STORAGE_KEY, id)
            }
        },
        [canSwitch]
    )

    const selectedLocation = useMemo(
        () => locations.find((l) => l.id === selectedLocationId) ?? null,
        [locations, selectedLocationId]
    )

    const value = useMemo<LocationContextValue>(
        () => ({
            locations,
            selectedLocationId,
            selectedLocation,
            setSelectedLocationId,
            isLoading,
            canSwitch,
            refresh: loadLocations,
        }),
        [locations, selectedLocationId, selectedLocation, setSelectedLocationId, isLoading, canSwitch, loadLocations]
    )

    return <LocationContext.Provider value={value}>{children}</LocationContext.Provider>
}

export function useLocationContext(): LocationContextValue {
    const ctx = useContext(LocationContext)
    if (!ctx) {
        throw new Error("useLocationContext must be used inside <LocationProvider>")
    }
    return ctx
}

/**
 * Convenience hook used by setup pages: returns the id to thread into
 * API helpers, or `undefined` when the context isn't ready yet.
 */
export function useSelectedLocationId(): string | undefined {
    const { selectedLocationId } = useLocationContext()
    return selectedLocationId ?? undefined
}
