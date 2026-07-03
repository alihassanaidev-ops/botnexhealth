#!/usr/bin/env bash
# Claude Code: PreToolUse hook.
# Injects first 30 lines of active session's task_plan.md to stderr for attention.
# No JSON output - Claude Code v2 treats exit 0 as implicit allow.
# Robust: never blocks, never fails, never emits anything that could fail schema validation.

set -u
set -o pipefail

LATEST_SESSION=""
if [ -d docs/sessions ]; then
    LATEST_SESSION=$(ls -1dt docs/sessions/*/ 2>/dev/null | head -1 || true)
fi

if [ -n "${LATEST_SESSION:-}" ] && [ -f "${LATEST_SESSION}task_plan.md" ]; then
    head -30 "${LATEST_SESSION}task_plan.md" >&2 2>/dev/null || true
fi

exit 0
