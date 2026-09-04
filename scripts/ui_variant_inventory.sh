#!/usr/bin/env bash
#
# Everything belonging to each of the two UI treatments, so dropping the one
# the team rejects is a checklist rather than an archaeology exercise.
#
#   scripts/ui_variant_inventory.sh [classic|refresh]
#
# Sites are tagged in the source with `@ui-variant classic` or
# `@ui-variant refresh`. Keep the tags accurate when touching either
# treatment — this script is only as good as they are.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
WEB="nexus-dashboard-web/src"

section() { printf '\n\033[1m%s\033[0m\n' "$1"; }

report() {
  local variant="$1"
  section "Files that exist only for '${variant}'"
  grep -rl "@ui-variant ${variant} — delete" "${WEB}" 2>/dev/null | sed 's/^/  /' || echo "  (none)"

  section "Individual sites tagged '${variant}'"
  grep -rn "@ui-variant ${variant}" "${WEB}" 2>/dev/null \
    | grep -v "delete this" | sed 's/^/  /' || echo "  (none)"
}

case "${1:-}" in
  classic)
    report classic
    section "Also remove"
    echo "  ${WEB}/lib/ui-mode.ts and its callers — with one treatment left there is nothing to switch between"
    echo "  the applyUiMode() call in ${WEB}/main.tsx"
    echo "  VITE_UI_MODE from vite-env.d.ts and any deployment that sets it"
    echo "  the icon={...} props on PageHeader, which exist as the classic fallback"
    echo "  the 'Switching the UI back' section of nexus-dashboard-web/README.md"
    ;;
  refresh)
    report refresh
    section "Also remove"
    echo "  ${WEB}/assets/icons/svg/ — the traced artwork"
    echo "  every art=\"...\" prop on PageHeader ($(grep -rho 'art="[a-zA-Z]*"' "${WEB}/pages" 2>/dev/null | wc -l | tr -d ' ') occurrences)"
    echo "  the art prop from ${WEB}/components/PageHeader.tsx"
    section "Move, don't delete"
    echo "  ${WEB}/styles/classic-ui.css holds the pre-refresh type scale, radii"
    echo "  and dark palette as overrides. Fold those values back into"
    echo "  index.css, replacing the refreshed ones, then delete the file."
    ;;
  *)
    echo "usage: $0 [classic|refresh]" >&2
    echo >&2
    echo "Prints everything belonging to one UI treatment, so it can be removed" >&2
    echo "cleanly once the team picks the other." >&2
    exit 64
    ;;
esac
