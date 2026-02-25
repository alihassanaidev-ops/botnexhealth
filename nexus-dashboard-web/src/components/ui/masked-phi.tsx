import { useState, useEffect } from "react"
import { Shield, Eye, EyeOff } from "lucide-react"

export interface MaskedPHIProps {
    value: string
    isPhi: boolean
    className?: string
}

export function MaskedPHI({ value, isPhi, className = "" }: MaskedPHIProps) {
    const [isRevealed, setIsRevealed] = useState(false)

    // Auto-hide the revealed value after 15 seconds
    useEffect(() => {
        let timeout: NodeJS.Timeout
        if (isRevealed) {
            timeout = setTimeout(() => {
                setIsRevealed(false)
            }, 10000)
        }
        return () => clearTimeout(timeout)
    }, [isRevealed])

    if (!isPhi) {
        return <span className={className}>{value}</span>
    }

    return (
        <div className={`flex items-center gap-2 group ${className}`}>
            <Shield className="h-3 w-3 text-amber-500 flex-shrink-0" />
            <button
                type="button"
                onClick={() => setIsRevealed(!isRevealed)}
                className="flex items-center gap-2 hover:bg-zinc-100 px-1 -mx-1 rounded transition-colors"
                title={isRevealed ? "Hide PHI" : "Reveal PHI"}
            >
                <span className={isRevealed ? "font-mono" : "text-zinc-500 tracking-[0.2em]"}>
                    {isRevealed ? value : "••••••••"}
                </span>
                {isRevealed ? (
                    <EyeOff className="h-3 w-3 text-zinc-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                ) : (
                    <Eye className="h-3 w-3 text-zinc-400 opacity-0 group-hover:opacity-100 transition-opacity" />
                )}
            </button>
        </div>
    )
}
