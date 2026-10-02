#!/usr/bin/env bash
# Rehearse `alembic upgrade head` from a git ref against the local copy of
# production, exactly as the production migration task runs it
# (python -m src.app.scripts.migrate_database; the app-role provisioning step
# is skipped locally because APP_ROLE_SECRET_ARN is unset).
#
# The ref is checked out into a temporary git worktree, so your working copy
# and current branch are untouched. The database is reset from the pristine
# production copy first (pass --no-reset to migrate on top of the last run).
#
# Results go to scripts/prod_db_rehearsal/runs/<stamp>-<ref>/ (gitignored):
#   plan.txt              revisions alembic will apply (current -> head)
#   migrate.log           full migration output
#   schema_before.sql / schema_after.sql      pg_dump --schema-only
#   policies_before.tsv / policies_after.tsv  pg_policies
#   rls_before.tsv / rls_after.tsv            tables and their RLS flags
#
# Usage:
#   scripts/prod_db_rehearsal/migrate_local.sh release/prod-merge
#   scripts/prod_db_rehearsal/migrate_local.sh origin/staging        # shows today's chain problem
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "${HERE}" rev-parse --show-toplevel)"
CONTAINER="nexhealth-prod-rehearsal-db"
DB="nexhealth"

RESET=1
REF=""
for arg in "$@"; do
  case "${arg}" in
    --no-reset) RESET=0 ;;
    *) REF="${arg}" ;;
  esac
done
[[ -n "${REF}" ]] || { echo "usage: migrate_local.sh <git ref | checkout dir> [--no-reset]" >&2; exit 1; }
# A directory means "use this checkout as-is" (e.g. a worktree with
# uncommitted migration edits); anything else must be a git ref.
SOURCE_DIR=""
if [[ -d "${REF}" ]]; then
  SOURCE_DIR="$(cd "${REF}" && pwd)"
  REF="dir:$(basename "${SOURCE_DIR}")"
elif ! git -C "${REPO}" rev-parse --verify --quiet "${REF}^{commit}" >/dev/null; then
  echo "unknown git ref or directory: ${REF}" >&2; exit 1
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${HERE}/runs/${STAMP}-${REF//[\/:]/_}"
mkdir -p "${RUN_DIR}"
WORKTREE="$(mktemp -d -t prod-rehearsal-XXXXXX)"
cleanup() { [[ -z "${SOURCE_DIR}" ]] && git -C "${REPO}" worktree remove --force "${WORKTREE}" >/dev/null 2>&1; true; }
trap cleanup EXIT

psql_db() { docker exec -i "${CONTAINER}" psql -U postgres -d "${DB}" "$@"; }
snapshot() {  # $1 = before|after
  docker exec -i "${CONTAINER}" pg_dump -U postgres --schema-only --no-owner "${DB}" > "${RUN_DIR}/schema_$1.sql"
  psql_db -AtF $'\t' -c "select schemaname, tablename, policyname, permissive, roles::text, cmd, regexp_replace(coalesce(qual, ''), '\\s+', ' ', 'g'), regexp_replace(coalesce(with_check, ''), '\\s+', ' ', 'g') from pg_policies order by 1,2,3" > "${RUN_DIR}/policies_$1.tsv"
  psql_db -AtF $'\t' -c "select c.relname, c.relrowsecurity, c.relforcerowsecurity from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind in ('r','p') order by 1" > "${RUN_DIR}/rls_$1.tsv"
}

[[ "${RESET}" == 1 ]] && "${HERE}/restore_local.sh" --reset

if [[ -n "${SOURCE_DIR}" ]]; then
  rmdir "${WORKTREE}"; WORKTREE="${SOURCE_DIR}"
  echo "checkout ${SOURCE_DIR} (base $(git -C "${WORKTREE}" log -1 --format='%h %s'), uncommitted: $(git -C "${WORKTREE}" status --porcelain | wc -l) files)" | tee "${RUN_DIR}/plan.txt"
else
  git -C "${REPO}" worktree add --detach --quiet "${WORKTREE}" "${REF}"
  echo "ref ${REF} = $(git -C "${WORKTREE}" log -1 --format='%h %s')" | tee "${RUN_DIR}/plan.txt"
fi

ADMIN_URL="postgresql+asyncpg://nexhealth_admin:rehearsal@127.0.0.1:55432/${DB}"
export DATABASE_ADMIN_URL="${ADMIN_URL}" DATABASE_URL="${ADMIN_URL}"
export APP_ENV=development REDIS_URL= CELERY_BROKER_URL=
export JWT_SECRET=local-rehearsal-placeholder  # config needs a value to load; unused by migrations
unset APP_ROLE_SECRET_ARN ALEMBIC_BASELINE_REVISION

cd "${WORKTREE}"
echo "installing dependencies for ${REF} (uv sync)"
uv sync --quiet --frozen --inexact 2>/dev/null || uv sync --quiet --inexact

BEFORE="$(psql_db -Atc 'select version_num from alembic_version')"
{
  echo "database at: ${BEFORE}"
  echo "alembic heads in ${REF}:"
  uv run alembic heads
  echo
  echo "revisions to apply (${BEFORE} -> head):"
  uv run alembic history -r "${BEFORE}:head" | tac
} | tee -a "${RUN_DIR}/plan.txt"

snapshot before
echo
echo "running migrations (python -m src.app.scripts.migrate_database) ..."
set +e
uv run python -m src.app.scripts.migrate_database > "${RUN_DIR}/migrate.log" 2>&1
STATUS=$?
set -e
snapshot after
AFTER="$(psql_db -Atc 'select version_num from alembic_version')"

echo
echo "================ result ================"
echo "ref:          ${REF}"
echo "before:       ${BEFORE}"
echo "after:        ${AFTER}"
echo "exit status:  ${STATUS}"
echo "tables:       $(wc -l < "${RUN_DIR}/rls_before.tsv") -> $(wc -l < "${RUN_DIR}/rls_after.tsv")"
echo "policies:     $(wc -l < "${RUN_DIR}/policies_before.tsv") -> $(wc -l < "${RUN_DIR}/policies_after.tsv")"
echo "tables without RLS after migrating:"
awk -F'\t' '$2 == "f" {print "  " $1}' "${RUN_DIR}/rls_after.tsv"
echo "policies removed or changed on tables that existed before:"
comm -23 <(sort "${RUN_DIR}/policies_before.tsv") <(sort "${RUN_DIR}/policies_after.tsv") | cut -f2,3 | sed 's/^/  /' || true
if [[ "${STATUS}" != 0 ]]; then
  echo
  echo "MIGRATION FAILED. Last lines of ${RUN_DIR}/migrate.log:"
  tail -25 "${RUN_DIR}/migrate.log"
fi
echo "full output: ${RUN_DIR}"
exit "${STATUS}"
