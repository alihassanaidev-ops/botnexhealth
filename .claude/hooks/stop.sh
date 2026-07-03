#!/usr/bin/env bash
# Claude Code: Stop hook.
# Auto-continues incomplete sessions via followup_message.
# Robust: defaults all counters; handles missing files; caps output; JSON is hand-assembled to avoid jq dep.

set -u
set -o pipefail

LATEST_SESSION=""
if [ -d docs/sessions ]; then
    LATEST_SESSION=$(ls -1dt docs/sessions/*/ 2>/dev/null | head -1 || true)
fi

# No active session: allow stop silently.
if [ -z "${LATEST_SESSION:-}" ]; then
    exit 0
fi

PLAN_FILE="${LATEST_SESSION}task_plan.md"
if [ ! -f "$PLAN_FILE" ]; then
    exit 0
fi

# Count phase markers. grep returns 1 on no-match; || echo 0 neutralises that.
TOTAL=$(grep -c "### Phase" "$PLAN_FILE" 2>/dev/null || echo 0)
COMPLETE=$(grep -cF "**Status:** complete" "$PLAN_FILE" 2>/dev/null || echo 0)
IN_PROGRESS=$(grep -cF "**Status:** in_progress" "$PLAN_FILE" 2>/dev/null || echo 0)
PENDING=$(grep -cF "**Status:** pending" "$PLAN_FILE" 2>/dev/null || echo 0)

# Fallback to inline [complete]/[in_progress]/[pending] format if **Status:** not found.
if [ "$COMPLETE" -eq 0 ] && [ "$IN_PROGRESS" -eq 0 ] && [ "$PENDING" -eq 0 ]; then
    COMPLETE=$(grep -c "\[complete\]" "$PLAN_FILE" 2>/dev/null || echo 0)
    IN_PROGRESS=$(grep -c "\[in_progress\]" "$PLAN_FILE" 2>/dev/null || echo 0)
    PENDING=$(grep -c "\[pending\]" "$PLAN_FILE" 2>/dev/null || echo 0)
fi

# Guard: no phases defined at all -> allow stop (avoid infinite loop on malformed plan).
if [ "$TOTAL" -eq 0 ]; then
    exit 0
fi

# Escape session path for JSON (backslashes + quotes).
SESSION_ESC=$(printf '%s' "$LATEST_SESSION" | sed 's/\\/\\\\/g; s/"/\\"/g')

if [ "$COMPLETE" -eq "$TOTAL" ]; then
    # CLAUDE.md §4: refresh the Graphify graph at end of session. Best-effort, non-blocking on error.
    (graphify update . >/dev/null 2>&1 || true) &
    disown 2>/dev/null || true
    printf '{"followup_message": "[planning-with-files] ALL PHASES COMPLETE (%s/%s) in %s. Graph update dispatched in background."}\n' "$COMPLETE" "$TOTAL" "$SESSION_ESC"
else
    printf '{"followup_message": "[planning-with-files] Task incomplete (%s/%s phases done) in %s. Update progress.md, re-read task_plan.md, continue remaining phases. To halt: set all phases complete or delete task_plan.md."}\n' "$COMPLETE" "$TOTAL" "$SESSION_ESC"
fi

exit 0
