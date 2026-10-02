/// <reference types="vite/client" />

interface ImportMetaEnv {
    readonly VITE_API_URL: string
    /** "refresh" (default) or "classic" — see @/lib/ui-mode. */
    readonly VITE_UI_MODE?: string
}

interface ImportMeta {
    readonly env: ImportMetaEnv
}
