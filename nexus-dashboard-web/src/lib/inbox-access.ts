import type { User } from "@/types"

/**
 * Roles that may open the conversation inbox.
 *
 * SUPER_ADMIN is deliberately absent: the platform operator does not read
 * patient conversations. The API refuses that role conversation content as
 * well (InboxScope.may_read_content), so hiding the page is not the only
 * barrier.
 */
export const INBOX_ROLES: User["role"][] = [
    "GROUP_ADMIN",
    "INSTITUTION_ADMIN",
    "LOCATION_ADMIN",
    "STAFF",
]
