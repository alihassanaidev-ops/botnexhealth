import type {
    ButtonHTMLAttributes,
    InputHTMLAttributes,
    ReactNode,
} from "react"

import "./auth-scaffold.css"

type AuthScaffoldProps = {
    children: ReactNode
}

export function AuthScaffold({ children }: AuthScaffoldProps) {
    return (
        <main className="auth-page">
            <div className="auth-orb auth-orb-one" aria-hidden="true" />
            <div className="auth-orb auth-orb-two" aria-hidden="true" />
            <div className="auth-layout">
                <section className="auth-workspace">
                    <div className="auth-mobile-brand">
                        <img src="/scalenexuslogo.svg" alt="ScaleNexus" />
                        <span>ScaleNexus<span>.AI</span></span>
                    </div>
                    <div className="auth-panel">{children}</div>
                </section>
            </div>
        </main>
    )
}

export function AuthHeader({
    title,
    description,
    icon,
}: {
    title: string
    description: ReactNode
    icon?: ReactNode
}) {
    return (
        <header className="auth-header">
            {icon ? <div className="auth-header-icon">{icon}</div> : null}
            <h2>{title}</h2>
            <div className="auth-description">{description}</div>
        </header>
    )
}

type AuthFieldProps = InputHTMLAttributes<HTMLInputElement> & {
    label: string
    error?: string
    hint?: string
}

export function AuthField({ label, error, hint, id, ...props }: AuthFieldProps) {
    const inputId = id ?? props.name
    const errorId = error && inputId ? `${inputId}-error` : undefined
    const hintId = hint && inputId ? `${inputId}-hint` : undefined
    const describedBy = [errorId, hintId].filter(Boolean).join(" ") || undefined

    return (
        <label className="auth-field" htmlFor={inputId}>
            <span className="auth-label">{label}</span>
            <input
                id={inputId}
                className="auth-input"
                aria-invalid={error ? "true" : undefined}
                aria-describedby={describedBy}
                {...props}
            />
            {hint ? <span className="auth-hint" id={hintId}>{hint}</span> : null}
            {error ? <span className="auth-error" id={errorId} role="alert">{error}</span> : null}
        </label>
    )
}

type AuthButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: "primary" | "secondary" | "quiet"
    fullWidth?: boolean
}

export function AuthButton({
    variant = "primary",
    fullWidth = true,
    className = "",
    ...props
}: AuthButtonProps) {
    return (
        <button
            className={`auth-button auth-button-${variant}${fullWidth ? " auth-button-full" : ""} ${className}`.trim()}
            {...props}
        />
    )
}

type AuthChoiceProps = ButtonHTMLAttributes<HTMLButtonElement> & {
    title: string
    description: string
    badge?: string
    icon?: ReactNode
}

export function AuthChoice({ title, description, badge, icon, ...props }: AuthChoiceProps) {
    return (
        <button className="auth-choice" {...props}>
            {icon ? <span className="auth-choice-icon">{icon}</span> : null}
            <span className="auth-choice-copy">
                <span className="auth-choice-title-row">
                    <strong>{title}</strong>
                    {badge ? <span className="auth-choice-badge">{badge}</span> : null}
                </span>
                <span>{description}</span>
            </span>
            <span className="auth-choice-arrow" aria-hidden="true">→</span>
        </button>
    )
}

export function AuthDivider({ label = "or" }: { label?: string }) {
    return <div className="auth-divider"><span>{label}</span></div>
}

export function AuthCodePanel({ children, mono = false }: { children: ReactNode; mono?: boolean }) {
    return <div className={`auth-code-panel${mono ? " auth-code-panel-mono" : ""}`}>{children}</div>
}
