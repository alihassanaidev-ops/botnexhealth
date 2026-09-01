/**
 * Dropdown for institution admins to switch the active location.
 *
 * Hidden entirely for users that can't switch (LOCATION_ADMIN, STAFF —
 * the backend pins them to user.location_id) and for institutions with
 * a single active location (no switching to do).
 */

import { useLocationContext } from "@/context/LocationContext"
import { UiSelect } from "@/components/foundation/Primitives"

export function LocationSelector() {
    const { locations, selectedLocationId, setSelectedLocationId, canSwitch, isLoading } =
        useLocationContext()

    if (!canSwitch) return null
    if (isLoading) return null
    if (locations.length === 0) return null
    if (locations.length === 1) {
        return (
            <div
                aria-label="Active location"
                className="shell-location-static"
                data-testid="location-selector"
                title={locations[0].name}
            >
                {locations[0].name}
            </div>
        )
    }

    return (
        <UiSelect
            aria-label="Active location"
            className="shell-location-select"
            data-testid="location-selector"
            value={selectedLocationId ?? ""}
            onChange={(event) => setSelectedLocationId(event.target.value)}
        >
            <option value="" disabled>Select location</option>
            {locations.map((loc) => (
                <option key={loc.id} value={loc.id}>{loc.name}</option>
            ))}
        </UiSelect>
    )
}
