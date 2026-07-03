#!/usr/bin/env bash
# Claude Code: PostToolUse hook.
# Reminds the model to update active session's progress.md / task_plan.md after a file mod.
# Robust: no JSON output, never fails.

set -u
set -o pipefail

LATEST_SESSION=""
if [ -d docs/sessions ]; then
    LATEST_SESSION=$(ls -1dt docs/sessions/*/ 2>/dev/null | head -1 || true)
fi

if [ -n "${LATEST_SESSION:-}" ] && [ -f "${LATEST_SESSION}task_plan.md" ]; then
    echo "[planning-with-files] Session ${LATEST_SESSION}: update progress.md with what you just did. If a phase is complete, mark it in task_plan.md."
fi

exit 0
