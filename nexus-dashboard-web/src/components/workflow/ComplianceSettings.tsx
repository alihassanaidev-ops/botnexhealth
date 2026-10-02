/**
 * Campaign compliance settings — sets the workflow's `compliance` block
 * (`content_class` + `consent_required`), which the backend validation service
 * uses for consent-path and content-class checks. Setting a content class
 * resolves the `content_class_unset` warning surfaced in the validation panel.
 */
import { ShieldCheck } from "lucide-react"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import type { ComplianceMetadata, ContentClass } from "@/types/workflow"

const CONTENT_CLASSES: { value: ContentClass; label: string }[] = [
    { value: "transactional_care", label: "Transactional care" },
    { value: "recall", label: "Recall" },
    { value: "sales", label: "Sales" },
    { value: "marketing", label: "Marketing" },
]

const UNSET = "__unset__"

export interface ComplianceSettingsProps {
    compliance: ComplianceMetadata | null | undefined
    onChange: (next: ComplianceMetadata) => void
    disabled?: boolean
}

export default function ComplianceSettings({ compliance, onChange, disabled }: ComplianceSettingsProps) {
    const contentClass = compliance?.content_class ?? null
    // Backend default is consent_required=true; reflect that when unset.
    const consentRequired = compliance?.consent_required ?? true

    return (
        <div className="space-y-3 rounded-md border border-border p-3">
            <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                <ShieldCheck className="h-3.5 w-3.5" /> Compliance
            </h3>

            <div className="space-y-1.5">
                <Label className="text-xs">Content class</Label>
                <Select
                    disabled={disabled}
                    value={contentClass ?? UNSET}
                    onValueChange={(v) =>
                        onChange({
                            content_class: v === UNSET ? null : (v as ContentClass),
                            consent_required: consentRequired,
                        })
                    }
                >
                    <SelectTrigger className="h-8 text-xs">
                        <SelectValue placeholder="Not set" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value={UNSET} className="text-xs">
                            Not set
                        </SelectItem>
                        {CONTENT_CLASSES.map((c) => (
                            <SelectItem key={c.value} value={c.value} className="text-xs">
                                {c.label}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>

            <div className="flex items-center justify-between">
                <Label htmlFor="consent-required" className="text-xs">
                    Consent required
                </Label>
                <Switch
                    id="consent-required"
                    disabled={disabled}
                    checked={consentRequired}
                    onCheckedChange={(checked) =>
                        onChange({ content_class: contentClass, consent_required: checked })
                    }
                />
            </div>
        </div>
    )
}
