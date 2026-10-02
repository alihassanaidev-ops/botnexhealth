import { PeopleDirectory } from "@/pages/Patients"

/**
 * Relationship workspace for people who are not yet linked to the PMS.
 *
 * Patients use the same underlying Contact identity, but have their own
 * care-system projection at /patients. Keeping the two page modes in one
 * component prevents masking, scoping, merging, and detail behavior from
 * drifting between two copies of the same person UI.
 */
export default function Contacts() {
    return <PeopleDirectory mode="contacts" />
}
