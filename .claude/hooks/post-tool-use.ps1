# Claude Code: PostToolUse hook (PowerShell).
# Robust: errors suppressed; never fails.

$ErrorActionPreference = "SilentlyContinue"

try {
    $latest = Get-ChildItem -Path "docs\sessions" -Directory -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending |
              Select-Object -First 1
    if ($latest -and (Test-Path "$($latest.FullName)\task_plan.md")) {
        Write-Output "[planning-with-files] Session $($latest.FullName): update progress.md with what you just did. If a phase is complete, mark it in task_plan.md."
    }
} catch { }

exit 0
