import { cn } from "@/lib/utils"

// App-shell loading state: the full-color ScaleNexus logo (no background) with a
// glossy "shine" sweeping across it. The shine is a moving highlight masked to
// the logo's exact shape, so it gleams only over the mark + wordmark.
export default function BrandLoader({ fullScreen = false, className }: { fullScreen?: boolean; className?: string }) {
    return (
        <div
            className={cn(
                "flex w-full items-center justify-center",
                fullScreen ? "min-h-screen" : "min-h-[60vh]",
                className,
            )}
            role="status"
            aria-label="Loading"
        >
            <div className="relative inline-block">
                <img src="/scalenexuslogo.png" alt="ScaleNexus" className="w-44 select-none" draggable={false} />
                <span className="brand-shine pointer-events-none absolute inset-0" aria-hidden="true" />
            </div>
            <span className="sr-only">Loading…</span>

            <style>{`
                .brand-shine {
                    background-image: linear-gradient(105deg, transparent 42%, rgba(255,255,255,.9) 50%, transparent 58%);
                    background-size: 200% 100%;
                    background-repeat: no-repeat;
                    -webkit-mask: url(/scalenexuslogo.png) center / contain no-repeat;
                            mask: url(/scalenexuslogo.png) center / contain no-repeat;
                    animation: brandShine 1.9s ease-in-out infinite;
                }
                @keyframes brandShine {
                    0%, 12%   { background-position: 160% 0; }
                    88%, 100% { background-position: -60% 0; }
                }
                @media (prefers-reduced-motion: reduce) { .brand-shine { animation: none; opacity: 0; } }
            `}</style>
        </div>
    )
}
