#!/usr/bin/env bash
# Option C: build production's schema locally with NO production access and
# NO patient data, by running the production branch's own migrations on an
# empty database. The result becomes the pristine template that
# migrate_local.sh resets from, exactly like a restored dump would.
#
# Catches: chain/order problems, SQL that fails against production's schema,
# missing tables/functions, RLS/policy changes.
# Does NOT catch: data-dependent failures (duplicate rows, CHECK/NOT NULL
# violations, long locks on big tables). Use a real dump (restore_local.sh)
# for those.
#
# Usage:
#   scripts/prod_db_rehearsal/bootstrap_schema_only.sh [git ref, default origin/production]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "${HERE}" rev-parse --show-toplevel)"
REF="${1:-origin/production}"
COMPOSE=(docker compose -f "${HERE}/docker-compose.yml")
CONTAINER="nexhealth-prod-rehearsal-db"
PRISTINE="nexhealth_prod_pristine"

psql_su() { docker exec -i "${CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${1:-postgres}" "${@:2}"; }

"${COMPOSE[@]}" up -d
for _ in $(seq 1 40); do
  docker exec "${CONTAINER}" pg_isready -U postgres >/dev/null 2>&1 && break
  sleep 1
done

echo "creating roles (RDS-like: master is not a superuser but has BYPASSRLS, as production needs for app_rls_definer; app role cannot bypass RLS)"
psql_su postgres -q <<'SQL'
do $$ begin create role nexhealth_admin; exception when duplicate_object then null; end $$;
do $$ begin create role nexhealth_app; exception when duplicate_object then null; end $$;
alter role nexhealth_admin with login nosuperuser createrole createdb bypassrls password 'rehearsal';
alter role nexhealth_app with login nosuperuser nobypassrls password 'rehearsal';
SQL
# PG16: a CREATEROLE role only gets ADMIN on roles it creates, not SET. The
# migrations create app_rls_definer and then hand functions to it, which works
# on RDS; mirror that by self-granting SET/INHERIT on roles the master creates.
psql_su postgres -qc "alter system set createrole_self_grant = 'set, inherit'"
psql_su postgres -qc "select pg_reload_conf()" >/dev/null

psql_su postgres -qc "drop database if exists nexhealth"
psql_su postgres -qc "update pg_database set datistemplate = false where datname = '${PRISTINE}'" >/dev/null
psql_su postgres -qc "drop database if exists ${PRISTINE}"
psql_su postgres -qc "create database ${PRISTINE} owner nexhealth_admin"
# Production's migrations hand functions to app_rls_definer, which requires
# CREATE on schema public; that succeeds on RDS, so its public schema allows
# it. Mirror that here (verify against the real dump's ACL in option A).
psql_su "${PRISTINE}" -qc "grant create on schema public to public"

WORKTREE="$(mktemp -d -t prod-rehearsal-XXXXXX)"
trap 'git -C "${REPO}" worktree remove --force "${WORKTREE}" >/dev/null 2>&1 || true' EXIT
git -C "${REPO}" worktree add --detach --quiet "${WORKTREE}" "${REF}"
echo "building schema from ${REF} = $(git -C "${WORKTREE}" log -1 --format='%h %s')"

# Production's baseline runs Base.metadata.create_all with the models as of
# the checked-out code, so on an EMPTY database later migrations that create
# the same tables fail ("already exists"). The live production DB never hit
# this because it was migrated incrementally. Staging fixed it by adding
# IF NOT EXISTS guards to these two files; borrow only the guards (keep this
# ref's down_revision) so the chain replays. Temporary worktree only.
GUARD_SOURCE_REF="${GUARD_SOURCE_REF:-origin/staging}"
for f in 20260829_external_sms_notification_recipients.py 20260830_sms_recipient_location_scope.py; do
  path="alembic/versions/${f}"
  [[ -f "${WORKTREE}/${path}" ]] || continue
  original_parent="$(grep -E '^down_revision' "${WORKTREE}/${path}")"
  git -C "${REPO}" show "${GUARD_SOURCE_REF}:${path}" > "${WORKTREE}/${path}"
  sed -i "s|^down_revision.*|${original_parent}|" "${WORKTREE}/${path}"
  echo "  replay guards for ${f} taken from ${GUARD_SOURCE_REF} (kept ${original_parent})"
done

ADMIN_URL="postgresql+asyncpg://nexhealth_admin:rehearsal@127.0.0.1:55432/${PRISTINE}"
export DATABASE_ADMIN_URL="${ADMIN_URL}" DATABASE_URL="${ADMIN_URL}"
export APP_ENV=development REDIS_URL= CELERY_BROKER_URL=
export JWT_SECRET=local-rehearsal-placeholder  # config needs a value to load; unused by migrations
unset APP_ROLE_SECRET_ARN ALEMBIC_BASELINE_REVISION

cd "${WORKTREE}"
uv sync --quiet --frozen --inexact 2>/dev/null || uv sync --quiet --inexact
uv run python -m src.app.scripts.migrate_database

psql_su "${PRISTINE}" -qc "analyze"
psql_su postgres -qc "update pg_database set datistemplate = true where datname = '${PRISTINE}'" >/dev/null
psql_su postgres -qc "create database nexhealth template ${PRISTINE} owner nexhealth_admin"
echo
echo "pristine schema-only copy ready. alembic_version:"
psql_su nexhealth -Atc "select version_num from alembic_version"
echo "tables: $(psql_su nexhealth -Atc "select count(*) from pg_tables where schemaname = 'public'")"
