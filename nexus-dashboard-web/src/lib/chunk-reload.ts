// A deploy replaces content-hashed JS chunks and deletes the old ones. A tab
// left open across a deploy fails to lazy-load a now-missing route chunk, which
// otherwise strands the user on the error screen. We auto-reload once to fetch
// the fresh build. The timestamp guard prevents a reload loop on a genuinely
// broken deploy (e.g. a chunk that 404s every time).

const CHUNK_RELOAD_KEY = "chunkReloadTs"

export function recoverFromChunkError(): boolean {
    const last = Number(sessionStorage.getItem(CHUNK_RELOAD_KEY) || 0)
    if (Date.now() - last > 10_000) {
        sessionStorage.setItem(CHUNK_RELOAD_KEY, String(Date.now()))
        window.location.reload()
        return true
    }
    return false
}

// Vite fires `vite:preloadError` when a dynamic import() of a chunk fails.
export function installChunkErrorReload(): void {
    window.addEventListener("vite:preloadError", ((e: Event) => {
        e.preventDefault()
        recoverFromChunkError()
    }) as EventListener)
}
