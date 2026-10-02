#!/usr/bin/env bash
# Restore a production dump into the local rehearsal Postgres (16.14, Docker).
#
# The dump is restored once into a template database `nexhealth_prod_pristine`,
# then cloned to `nexhealth`. Re-running with --reset recreates `nexhealth`
# from the pristine template in seconds, so every migration attempt starts
# from an exact copy of production.
#
# Roles come from roles.sql (pg_dumpall --roles-only --no-role-passwords), so
# nexhealth_admin / nexhealth_app keep production's attributes (NOSUPERUSER,
# NOBYPASSRLS, …). RDS-only roles (rds_superuser, rdsadmin, …) are created as
# plain stubs so GRANTs referencing them restore cleanly. Local passwords are
# set to "rehearsal".
#
# Usage:
#   scripts/prod_db_rehearsal/restore_local.sh ~/Downloads/scalenexus-prod-db/<stamp>
#   scripts/prod_db_rehearsal/restore_local.sh --reset
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE=(docker compose -f "${HERE}/docker-compose.yml")
CONTAINER="nexhealth-prod-rehearsal-db"
PRISTINE="nexhealth_prod_pristine"
TARGET="nexhealth"

psql_su() { docker exec -i "${CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d "${1:-postgres}" "${@:2}"; }

start_db() {
  "${COMPOSE[@]}" up -d
  for _ in $(seq 1 40); do
    docker exec "${CONTAINER}" pg_isready -U postgres >/dev/null 2>&1 && return
    sleep 1
  done
  echo "local rehearsal Postgres did not become ready" >&2; exit 1
}

clone_from_pristine() {
  psql_su postgres -qc "select pg_terminate_backend(pid) from pg_stat_activity where datname = '${TARGET}' and pid <> pg_backend_pid()" >/dev/null
  psql_su postgres -qc "drop database if exists ${TARGET}"
  psql_su postgres -qc "create database ${TARGET} template ${PRISTINE} owner nexhealth_admin"
  echo "fresh '${TARGET}' cloned from '${PRISTINE}'. alembic_version:"
  psql_su "${TARGET}" -Atc "select version_num from alembic_version"
}

if [[ "${1:-}" == "--reset" ]]; then
  start_db
  clone_from_pristine
  exit 0
fi

DUMP_DIR="${1:?usage: restore_local.sh <dump dir> | --reset}"
[[ -f "${DUMP_DIR}/prod.dump" && -f "${DUMP_DIR}/roles.sql" ]] || {
  echo "expected prod.dump and roles.sql in ${DUMP_DIR}" >&2; exit 1; }
if [[ -f "${DUMP_DIR}/SHA256SUMS" ]]; then
  (cd "${DUMP_DIR}" && sha256sum -c --quiet SHA256SUMS) || { echo "checksum mismatch" >&2; exit 1; }
fi

start_db

echo "creating roles"
# Stub every role roles.sql mentions first, so CREATE/ALTER/GRANT lines that
# reference RDS-managed roles never fail on a missing role.
grep -oE '^CREATE ROLE [a-zA-Z0-9_]+' "${DUMP_DIR}/roles.sql" | awk '{print $3}' | while read -r role; do
  [[ "${role}" == "postgres" ]] && continue
  psql_su postgres -qc "do \$\$ begin create role ${role}; exception when duplicate_object then null; end \$\$;"
done
# Apply production's role attributes and memberships. Lines that only make
# sense on RDS (e.g. rds-specific GUCs) are allowed to fail.
grep -vE '^(CREATE ROLE postgres|ALTER ROLE postgres )' "${DUMP_DIR}/roles.sql" \
  | docker exec -i "${CONTAINER}" psql -q -U postgres -d postgres -v ON_ERROR_STOP=0 2>&1 \
  | grep -vE 'already exists|^$' || true
psql_su postgres -qc "alter role nexhealth_admin with login password 'rehearsal'"
psql_su postgres -qc "alter role nexhealth_app with login password 'rehearsal'"
# Emulate what rds_superuser membership lets the RDS master do, which a plain
# NOSUPERUSER role cannot do locally: create BYPASSRLS roles (app_rls_definer),
# act as the roles it creates (createrole_self_grant), and hand functions to
# app_rls_definer (CREATE on schema public, granted after the restore).
psql_su postgres -qc "alter role nexhealth_admin with createrole createdb bypassrls"
psql_su postgres -qc "alter system set createrole_self_grant = 'set, inherit'"
psql_su postgres -qc "select pg_reload_conf()" >/dev/null
psql_su postgres -qc "grant app_rls_definer to nexhealth_admin with set true, inherit true" 2>/dev/null || true

echo "restoring into ${PRISTINE} (this can take a few minutes)"
psql_su postgres -qc "drop database if exists ${TARGET}"
psql_su postgres -qc "update pg_database set datistemplate = false where datname = '${PRISTINE}'" >/dev/null
psql_su postgres -qc "drop database if exists ${PRISTINE}"
psql_su postgres -qc "create database ${PRISTINE} owner nexhealth_admin"
docker exec -i "${CONTAINER}" pg_restore -U postgres -d "${PRISTINE}" \
  --no-subscriptions < "${DUMP_DIR}/prod.dump" 2> "${DUMP_DIR}/restore.log" || true
# Report restore errors other than the expected RDS-only ones.
if grep -E '^pg_restore: error' "${DUMP_DIR}/restore.log" | grep -vE 'rds|rdsadmin|rds_superuser' ; then
  echo "WARNING: unexpected pg_restore errors above (full log: ${DUMP_DIR}/restore.log)"
fi
psql_su "${PRISTINE}" -qc "grant create on schema public to app_rls_definer" 2>/dev/null || true
psql_su "${PRISTINE}" -qc "analyze"
psql_su postgres -qc "update pg_database set datistemplate = true where datname = '${PRISTINE}'" >/dev/null

clone_from_pristine
echo
echo "row counts (compare with manifest.txt):"
for t in calls contacts institutions institution_locations users notifications; do
  printf '  %-24s %s\n' "${t}" "$(psql_su "${TARGET}" -Atc "select count(*) from ${t}")"
done
