import { cn } from "@/lib/utils"

// App-shell loading state: the ScaleNexus mark in a brand gradient badge with a
// gentle "breathing" animation + an indeterminate bar. The logo SVG renders
// white, so the gradient badge guarantees contrast in both light and dark.
export default function BrandLoader({ fullScreen = false, className }: { fullScreen?: boolean; className?: string }) {
    return (
        <div
            className={cn(
                "flex w-full flex-col items-center justify-center gap-5",
                fullScreen ? "min-h-screen" : "min-h-[60vh]",
                className,
            )}
            role="status"
            aria-label="Loading"
        >
            <div className="relative">
                <div className="brand-loader-glow absolute inset-0 -z-10 rounded-3xl bg-gradient-to-br from-violet-500/40 to-purple-600/40 blur-2xl" />
                <div className="brand-loader-badge grid size-20 place-items-center rounded-3xl bg-gradient-to-br from-violet-500 to-purple-600 shadow-lg shadow-violet-500/25">
                    <img src="/scalenexuslogo.svg" alt="ScaleNexus" className="size-11 object-contain" draggable={false} />
                </div>
            </div>
            <div className="h-1 w-24 overflow-hidden rounded-full bg-muted">
                <div className="brand-loader-bar h-full w-1/2 rounded-full bg-gradient-to-r from-violet-500 to-purple-600" />
            </div>
            <span className="sr-only">Loading…</span>

            <style>{`
                @keyframes brandBreath { 0%,100% { transform: scale(1); opacity: 1 } 50% { transform: scale(1.06); opacity: .88 } }
                @keyframes brandGlow   { 0%,100% { opacity: .45 } 50% { opacity: .85 } }
                @keyframes brandBar    { 0% { transform: translateX(-120%) } 100% { transform: translateX(260%) } }
                .brand-loader-badge { animation: brandBreath 1.6s ease-in-out infinite }
                .brand-loader-glow  { animation: brandGlow 1.6s ease-in-out infinite }
                .brand-loader-bar   { animation: brandBar 1.25s ease-in-out infinite }
                @media (prefers-reduced-motion: reduce) {
                    .brand-loader-badge, .brand-loader-glow, .brand-loader-bar { animation: none }
                }
            `}</style>
        </div>
    )
}
