# Claude Code: Stop hook (PowerShell).
# Auto-continues incomplete sessions.
# Robust: defaults counters; handles missing files; JSON emitted via ConvertTo-Json.

$ErrorActionPreference = "SilentlyContinue"

function Send-Followup {
    param([string]$Message)
    $payload = @{ followup_message = $Message } | ConvertTo-Json -Compress
    Write-Host $payload
}

try {
    $latest = Get-ChildItem -Path "docs\sessions" -Directory -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending |
              Select-Object -First 1
    if (-not $latest) { exit 0 }

    $PlanFile = "$($latest.FullName)\task_plan.md"
    if (-not (Test-Path $PlanFile)) { exit 0 }

    $content = Get-Content $PlanFile -Raw -ErrorAction SilentlyContinue
    if (-not $content) { exit 0 }

    $TOTAL       = ([regex]::Matches($content, "### Phase")).Count
    $COMPLETE    = ([regex]::Matches($content, "\*\*Status:\*\* complete")).Count
    $IN_PROGRESS = ([regex]::Matches($content, "\*\*Status:\*\* in_progress")).Count
    $PENDING     = ([regex]::Matches($content, "\*\*Status:\*\* pending")).Count

    if ($COMPLETE -eq 0 -and $IN_PROGRESS -eq 0 -and $PENDING -eq 0) {
        $COMPLETE    = ([regex]::Matches($content, "\[complete\]")).Count
        $IN_PROGRESS = ([regex]::Matches($content, "\[in_progress\]")).Count
        $PENDING     = ([regex]::Matches($content, "\[pending\]")).Count
    }

    # No phases defined -> allow stop.
    if ($TOTAL -eq 0) { exit 0 }

    if ($COMPLETE -eq $TOTAL) {
        # CLAUDE.md §4: refresh the Graphify graph at session end. Best-effort, backgrounded.
        Start-Process -FilePath "graphify" -ArgumentList "update", "." `
            -WindowStyle Hidden -NoNewWindow:$false -ErrorAction SilentlyContinue | Out-Null
        Send-Followup "[planning-with-files] ALL PHASES COMPLETE ($COMPLETE/$TOTAL) in $($latest.Name). Graph update dispatched in background."
    } else {
        Send-Followup "[planning-with-files] Task incomplete ($COMPLETE/$TOTAL phases done) in $($latest.Name). Update progress.md, re-read task_plan.md, continue remaining phases. To halt: set all phases complete or delete task_plan.md."
    }
} catch { }

exit 0
