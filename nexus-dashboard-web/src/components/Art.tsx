// @ui-variant refresh — delete this file to drop the refreshed UI.
import type { ReactElement } from "react"
import { pageArt, type PageArtName } from "@/assets/icons"

/**
 * Render one illustrated icon inline.
 *
 * The markup must be inline rather than an <img> so the fills can resolve the
 * theme's CSS variables — see the note in @/assets/icons.
 *
 * dangerouslySetInnerHTML is safe here in the way the name warns about: the
 * markup is a build-time asset from this repo, never anything a user supplies.
 */
export function Art({
    name,
    className,
}: {
    name: PageArtName
    className?: string
}): ReactElement {
    return (
        <span
            className={className}
            aria-hidden="true"
            dangerouslySetInnerHTML={{ __html: pageArt[name] }}
        />
    )
}
