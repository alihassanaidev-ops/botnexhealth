/**
 * Custom Fields API service.
 *
 * CRUD operations for institution custom field definitions.
 */

import api from "@/lib/api";
import type { CustomFieldDefinition } from "@/types";

export interface CreateFieldDefinitionPayload {
    field_name: string;
    field_key: string;
    field_type?: string;
    entity_type?: string;
    is_phi?: boolean;
    is_required?: boolean;
    dropdown_options?: string[];
    retell_source?: string;
    retell_source_key?: string;
    display_order?: number;
}

export interface UpdateFieldDefinitionPayload {
    field_name?: string;
    field_type?: string;
    is_phi?: boolean;
    is_required?: boolean;
    dropdown_options?: string[];
    retell_source?: string;
    retell_source_key?: string;
    display_order?: number;
}

export async function listFieldDefinitions(
    entityType = "call",
    includeInactive = false,
): Promise<CustomFieldDefinition[]> {
    const params = new URLSearchParams();
    params.set("entity_type", entityType);
    if (includeInactive) params.set("include_inactive", "true");
    const { data } = await api.get<CustomFieldDefinition[]>(
        `/institution/custom-fields/definitions?${params.toString()}`,
    );
    return data;
}

export async function createFieldDefinition(
    payload: CreateFieldDefinitionPayload,
): Promise<CustomFieldDefinition> {
    const { data } = await api.post<CustomFieldDefinition>(
        "/institution/custom-fields/definitions",
        payload,
    );
    return data;
}

export async function updateFieldDefinition(
    id: string,
    payload: UpdateFieldDefinitionPayload,
): Promise<CustomFieldDefinition> {
    const { data } = await api.patch<CustomFieldDefinition>(
        `/institution/custom-fields/definitions/${id}`,
        payload,
    );
    return data;
}

export async function deactivateFieldDefinition(
    id: string,
    hard = false,
): Promise<void> {
    const url = hard
        ? `/institution/custom-fields/definitions/${id}?hard=true`
        : `/institution/custom-fields/definitions/${id}`;
    await api.delete(url);
}
