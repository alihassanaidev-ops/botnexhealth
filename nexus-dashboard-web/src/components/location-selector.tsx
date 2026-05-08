/**
 * Dropdown for institution admins to switch the active location.
 *
 * Hidden entirely for users that can't switch (LOCATION_ADMIN, STAFF —
 * the backend pins them to user.location_id) and for institutions with
 * a single active location (no switching to do).
 */

import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { useLocationContext } from "@/context/LocationContext"

export function LocationSelector() {
    const { locations, selectedLocationId, setSelectedLocationId, canSwitch, isLoading } =
        useLocationContext()

    if (!canSwitch) return null
    if (isLoading) return null
    if (locations.length <= 1) return null

    return (
        <Select
            value={selectedLocationId ?? undefined}
            onValueChange={setSelectedLocationId}
        >
            <SelectTrigger
                aria-label="Active location"
                className="h-8 w-full text-xs"
                data-testid="location-selector"
            >
                <SelectValue placeholder="Select location" />
            </SelectTrigger>
            <SelectContent>
                {locations.map((loc) => (
                    <SelectItem key={loc.id} value={loc.id}>
                        {loc.name}
                    </SelectItem>
                ))}
            </SelectContent>
        </Select>
    )
}
