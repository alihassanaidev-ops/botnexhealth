#!/usr/bin/env bash
# Production-grade local validation for the EventBridge-scheduled jobs.
#
# Runs four distinct fidelity layers in order — fastest first, slowest
# last. Each layer fails independently with a clear message so you know
# which layer caught the bug.
#
#   1. Module imports          — module-level side effects, fast
#   2. Script standalone       — your venv, your local DB
#   3. pytest integration      — seeded fixtures, real subprocess invocations
#   4. Docker production image — same artifact ECS RunTask uses
#   5. cdk synth               — verifies the EventBridge + ECS task wiring
#
# Layers 4 and 5 are gated behind ``--full`` so the day-to-day dev loop
# stays fast (~10s); ship-readiness check is ``--full`` (~3 min).
#
# Usage:
#   scripts/test_scheduled_jobs.sh           # quick (layers 1-3)
#   scripts/test_scheduled_jobs.sh --full    # all five layers
#
# Requirements:
#   - .env.local configured with DATABASE_URL + DATABASE_ADMIN_URL
#   - Local Postgres reachable from those URLs, with the schema migrated
#   - Local Redis (only relevant for the queue-metrics job, which uses
#     Redis; rollup + cleanup don't)
#   - For ``--full``: Docker, AWS CDK CLI, infra/.venv with cdk deps

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

GREEN=$'\033[0;32m'
RED=$'\033[0;31m'
YELLOW=$'\033[1;33m'
DIM=$'\033[2m'
RESET=$'\033[0m'

FULL_MODE=0
for arg in "$@"; do
    case "$arg" in
        --full) FULL_MODE=1 ;;
        -h|--help)
            sed -n '2,32p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

step() { echo "${YELLOW}━━━ $1 ${RESET}"; }
ok() { echo "${GREEN}✓ $1${RESET}"; }
fail() { echo "${RED}✗ $1${RESET}"; exit 1; }
note() { echo "${DIM}  $1${RESET}"; }

# Source local env if present so this script can be run with a single
# command.
if [ -f .env.local ]; then
    set -a; . .env.local; set +a
fi

if [ -z "${DATABASE_URL:-}" ] && [ -z "${DATABASE_ADMIN_URL:-}" ]; then
    fail "DATABASE_URL or DATABASE_ADMIN_URL must be set (source .env.local first)"
fi

VENV_PY="${VENV_PY:-.venv/bin/python}"
if [ ! -x "$VENV_PY" ]; then
    fail "Python venv not found at $VENV_PY (set VENV_PY env var to override)"
fi

SCRIPTS=(
    "src.app.scripts.recompute_dashboard_rollup"
    "src.app.scripts.cleanup_idempotency"
    "src.app.scripts.ensure_audit_partitions"
)

# ─── Layer 1: module imports cleanly ─────────────────────────────────────
step "Layer 1: import each scheduled-job module"
for mod in "${SCRIPTS[@]}"; do
    if "$VENV_PY" -c "import importlib; importlib.import_module('$mod')" 2>/dev/null; then
        ok "$mod imports cleanly"
    else
        "$VENV_PY" -c "import importlib; importlib.import_module('$mod')" || true
        fail "$mod has an import error — fix before continuing"
    fi
done

# ─── Layer 2: script standalone (venv + local DB) ────────────────────────
step "Layer 2: run each script standalone against your local DB"
for mod in "${SCRIPTS[@]}"; do
    note "running $mod"
    if "$VENV_PY" -m "$mod"; then
        ok "$mod exited 0"
    else
        fail "$mod exited non-zero — check stderr above"
    fi
done

# ─── Layer 3: pytest integration suite ────────────────────────────────────
step "Layer 3: pytest integration tests (seeds data, asserts post-conditions)"
"$VENV_PY" -m pytest tests/integration/test_scheduled_jobs.py -v --no-header
ok "integration tests passed"

if [ "$FULL_MODE" -ne 1 ]; then
    echo
    ok "Quick validation passed. Run with ${YELLOW}--full${RESET} for Docker + cdk synth checks before deploy."
    exit 0
fi

# ─── Layer 4: production Docker image ─────────────────────────────────────
step "Layer 4: run each script inside the production Docker image"
note "building image (uses repo Dockerfile)"
docker build -t nex-health-local-validate . >/dev/null

for mod in "${SCRIPTS[@]}"; do
    note "running $mod inside the production image"
    # --network host gives the container access to localhost Postgres/Redis
    # on macOS/Linux dev machines. The env-file forwards every relevant
    # connection string and credential.
    if docker run --rm \
        --network host \
        --env-file .env.local \
        nex-health-local-validate \
        python -m "$mod" >/tmp/scheduled-job-docker.log 2>&1; then
        ok "$mod exited 0 inside the production image"
    else
        echo "${DIM}--- container output ---${RESET}"
        cat /tmp/scheduled-job-docker.log
        echo "${DIM}------------------------${RESET}"
        fail "$mod failed inside the production image — packaging or env mismatch"
    fi
done

# ─── Layer 5: cdk synth verifies wiring ───────────────────────────────────
step "Layer 5: cdk synth — verify EventBridge + ECS task wiring is present"
INFRA_VENV="infra/.venv/bin"
if [ ! -d "$INFRA_VENV" ]; then
    fail "infra/.venv not found — run 'cd infra && python -m venv .venv && .venv/bin/pip install -r requirements.txt'"
fi

(
    cd infra
    PATH="$PWD/.venv/bin:$PATH" cdk synth --quiet 2>&1 | tail -3
)

# Extract the synthesized CFN template and grep for the resources we expect.
CFN=$(cd infra && PATH="$PWD/.venv/bin:$PATH" cdk synth 2>/dev/null)

REQUIRED_PATTERNS=(
    "RecomputeDashboardRollupSchedule"
    "CleanupIdempotencySchedule"
    "EnsureAuditPartitionsSchedule"
    "RecomputeDashboardRollupTaskDefinition"
    "CleanupIdempotencyTaskDefinition"
    "EnsureAuditPartitionsTaskDefinition"
)

for pattern in "${REQUIRED_PATTERNS[@]}"; do
    if grep -q "$pattern" <<< "$CFN"; then
        ok "cdk synth output contains $pattern"
    else
        fail "cdk synth missing $pattern — CDK wiring drift?"
    fi
done

echo
ok "${GREEN}All five layers passed. Safe to deploy.${RESET}"
