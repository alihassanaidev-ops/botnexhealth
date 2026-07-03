# Claude Code: UserPromptSubmit hook (PowerShell).
# Robust: errors suppressed; always exits 0.

$ErrorActionPreference = "SilentlyContinue"

Write-Output "[CLAUDE.md] Before first tool call: scan Map - Sec.2 classify (A/B/C/D), Sec.3 Graph BEFORE Grep/Read, Sec.4 Session folder for 3+ step work. Cite the section you're following."

try {
    $latest = Get-ChildItem -Path "docs\sessions" -Directory -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending |
              Select-Object -First 1
    if ($latest -and (Test-Path "$($latest.FullName)\task_plan.md")) {
        Write-Output "[planning-with-files] Active session: $($latest.FullName) - if not read this turn, read task_plan.md, progress.md, findings.md before proceeding."
    }
} catch { }

exit 0
