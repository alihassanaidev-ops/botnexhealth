#!/usr/bin/env bash
# Claude Code: UserPromptSubmit hook.
# Injects the CLAUDE.md Map reminder + active-session hint.
# Robust: never fails; all errors suppressed; safe under `set -u`.

set -u
set -o pipefail

echo "[CLAUDE.md] Before first tool call: scan Map - Sec.2 classify (A/B/C/D), Sec.3 Graph BEFORE Grep/Read, Sec.4 Session folder for 3+ step work. Cite the section you're following."

LATEST_SESSION=""
if [ -d docs/sessions ]; then
    LATEST_SESSION=$(ls -1dt docs/sessions/*/ 2>/dev/null | head -1 || true)
fi

if [ -n "${LATEST_SESSION:-}" ] && [ -f "${LATEST_SESSION}task_plan.md" ]; then
    echo "[planning-with-files] Active session: ${LATEST_SESSION} - if not read this turn, read task_plan.md, progress.md, findings.md before proceeding."
fi

exit 0
