/**
 * Palette for Workflow Statuses. Backend stores a palette *key* (see
 * src/app/models/workflow_status.py WORKFLOW_STATUS_COLORS); the UI maps it to
 * on-theme Tailwind classes here. Class strings are full literals so Tailwind's
 * JIT scanner keeps them (dynamically-assembled class names would get purged).
 */

export interface StatusColor {
    key: string
    label: string
    /** Badge classes (bg + text + border, light/dark). */
    badge: string
    /** Solid swatch for the color picker / rail dot. */
    swatch: string
}

export const STATUS_COLORS: StatusColor[] = [
    { key: "zinc", label: "Gray", badge: "bg-zinc-500/15 text-zinc-600 border-zinc-500/25 dark:text-zinc-300", swatch: "bg-zinc-500" },
    { key: "slate", label: "Slate", badge: "bg-slate-500/15 text-slate-600 border-slate-500/25 dark:text-slate-300", swatch: "bg-slate-500" },
    { key: "red", label: "Red", badge: "bg-red-500/15 text-red-600 border-red-500/25 dark:text-red-400", swatch: "bg-red-500" },
    { key: "orange", label: "Orange", badge: "bg-orange-500/15 text-orange-600 border-orange-500/25 dark:text-orange-400", swatch: "bg-orange-500" },
    { key: "amber", label: "Amber", badge: "bg-amber-500/15 text-amber-600 border-amber-500/25 dark:text-amber-400", swatch: "bg-amber-500" },
    { key: "yellow", label: "Yellow", badge: "bg-yellow-500/15 text-yellow-600 border-yellow-500/25 dark:text-yellow-400", swatch: "bg-yellow-500" },
    { key: "lime", label: "Lime", badge: "bg-lime-500/15 text-lime-600 border-lime-500/25 dark:text-lime-400", swatch: "bg-lime-500" },
    { key: "green", label: "Green", badge: "bg-green-500/15 text-green-600 border-green-500/25 dark:text-green-400", swatch: "bg-green-500" },
    { key: "emerald", label: "Emerald", badge: "bg-emerald-500/15 text-emerald-600 border-emerald-500/25 dark:text-emerald-400", swatch: "bg-emerald-500" },
    { key: "teal", label: "Teal", badge: "bg-teal-500/15 text-teal-600 border-teal-500/25 dark:text-teal-400", swatch: "bg-teal-500" },
    { key: "cyan", label: "Cyan", badge: "bg-cyan-500/15 text-cyan-600 border-cyan-500/25 dark:text-cyan-400", swatch: "bg-cyan-500" },
    { key: "sky", label: "Sky", badge: "bg-sky-500/15 text-sky-600 border-sky-500/25 dark:text-sky-400", swatch: "bg-sky-500" },
    { key: "blue", label: "Blue", badge: "bg-blue-500/15 text-blue-600 border-blue-500/25 dark:text-blue-400", swatch: "bg-blue-500" },
    { key: "indigo", label: "Indigo", badge: "bg-indigo-500/15 text-indigo-600 border-indigo-500/25 dark:text-indigo-400", swatch: "bg-indigo-500" },
    { key: "violet", label: "Violet", badge: "bg-violet-500/15 text-violet-600 border-violet-500/25 dark:text-violet-400", swatch: "bg-violet-500" },
    { key: "fuchsia", label: "Fuchsia", badge: "bg-fuchsia-500/15 text-fuchsia-600 border-fuchsia-500/25 dark:text-fuchsia-400", swatch: "bg-fuchsia-500" },
    { key: "pink", label: "Pink", badge: "bg-pink-500/15 text-pink-600 border-pink-500/25 dark:text-pink-400", swatch: "bg-pink-500" },
    { key: "rose", label: "Rose", badge: "bg-rose-500/15 text-rose-600 border-rose-500/25 dark:text-rose-400", swatch: "bg-rose-500" },
]

const STATUS_COLOR_MAP: Record<string, StatusColor> = Object.fromEntries(
    STATUS_COLORS.map((c) => [c.key, c]),
)

const FALLBACK = STATUS_COLORS[0]

export function statusBadgeClasses(colorKey: string | undefined): string {
    return (colorKey && STATUS_COLOR_MAP[colorKey]?.badge) || FALLBACK.badge
}

export function statusSwatchClass(colorKey: string | undefined): string {
    return (colorKey && STATUS_COLOR_MAP[colorKey]?.swatch) || FALLBACK.swatch
}
