# Claude Code: PreToolUse hook (PowerShell).
# Prints first 30 lines of active session's task_plan.md for attention.
# No JSON output - Claude Code v2 treats exit 0 as implicit allow.
# Robust: errors suppressed; never blocks.

$ErrorActionPreference = "SilentlyContinue"

try {
    $latest = Get-ChildItem -Path "docs\sessions" -Directory -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending |
              Select-Object -First 1
    if ($latest -and (Test-Path "$($latest.FullName)\task_plan.md")) {
        Get-Content "$($latest.FullName)\task_plan.md" -TotalCount 30 | Write-Host
    }
} catch { }

exit 0
