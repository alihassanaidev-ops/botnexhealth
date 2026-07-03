/**
 * Live message preview for send steps — renders merge tokens with sample data.
 * SMS shows a chat bubble + segment estimate; email shows subject + body.
 * Client-side only (no backend preview endpoint — findings.md §3).
 */
import { renderTemplate, smsSegments } from "@/lib/workflow/preview"
import type { SendEmailNode, SendSmsNode } from "@/types/workflow"

export function SmsPreview({ node }: { node: SendSmsNode }) {
    const rendered = renderTemplate(node.body_template)
    const segments = smsSegments(rendered)
    return (
        <div className="space-y-1.5">
            <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-blue-500 px-3 py-2 text-sm text-white shadow-sm">
                {rendered || <span className="italic opacity-70">No message yet</span>}
            </div>
            <p className="text-[10px] text-muted-foreground">
                {rendered.length} chars · {segments} segment{segments === 1 ? "" : "s"} · preview uses sample data
            </p>
        </div>
    )
}

export function EmailPreview({ node }: { node: SendEmailNode }) {
    return (
        <div className="overflow-hidden rounded-md border border-border">
            <div className="border-b border-border bg-muted/50 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Subject</div>
                <div className="text-sm font-medium">
                    {renderTemplate(node.subject_template) || <span className="italic text-muted-foreground">No subject</span>}
                </div>
            </div>
            <div className="whitespace-pre-wrap px-3 py-2.5 text-sm">
                {renderTemplate(node.body_template) || <span className="italic text-muted-foreground">No body</span>}
            </div>
        </div>
    )
}
