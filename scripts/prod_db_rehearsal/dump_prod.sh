#!/usr/bin/env bash
# Option A: read-only logical dump of the production database to this machine.
#
# How it reaches the private RDS instance (no permanent change):
#   1. Adds a temporary inline policy to the migration task role with the four
#      ssmmessages actions ECS Exec needs. Removed on exit.
#   2. Starts ONE one-off Fargate task from the migration task definition with
#      its command overridden to `sleep` (it never runs alembic), in the
#      migration security group that the DB security group already allows.
#   3. Opens an SSM port-forward through that task to the RDS endpoint.
#   4. Runs pg_dump / pg_dumpall (PostgreSQL 16.14 client, in Docker) over the
#      tunnel with the master credentials from Secrets Manager.
#   5. On exit (success, failure or Ctrl-C): closes the tunnel, stops the task,
#      deletes the temporary inline policy.
#
# Nothing in the database is modified; pg_dump reads inside one
# REPEATABLE READ, READ ONLY transaction.
#
# Output (directory 700, files 600). Contains PHI: keep it on an encrypted
# disk, never commit or share it, delete it when the rehearsal is over.
#   ~/Downloads/scalenexus-prod-db/<UTC stamp>/
#     prod.dump      custom-format pg_dump of the database
#     roles.sql      pg_dumpall --roles-only --no-role-passwords
#     manifest.txt   alembic revision, pre-checks, table sizes
#     SHA256SUMS
#
# Usage:
#   scripts/prod_db_rehearsal/dump_prod.sh            # asks for confirmation
#   CONFIRM="dump production" scripts/prod_db_rehearsal/dump_prod.sh
set -euo pipefail

STACK_NAME="${CDK_STACK_NAME:-nex-health-production}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-ca-central-1}}"
DB_NAME="${DB_NAME:-nexhealth}"
LOCAL_PORT="${LOCAL_PORT:-15432}"
PG_IMAGE="postgres:16.14"
POLICY_NAME="TempProdDbDumpEcsExec"
OUT_ROOT="${OUT_ROOT:-$HOME/Downloads/scalenexus-prod-db}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${OUT_ROOT}/${STAMP}"

aws_() { aws --region "${REGION}" "$@"; }
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

for bin in aws session-manager-plugin docker jq; do
  command -v "${bin}" >/dev/null || { echo "missing required tool: ${bin}" >&2; exit 1; }
done

stack_output() {
  aws_ cloudformation describe-stacks --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text
}

CLUSTER="$(stack_output ClusterName)"
TASK_DEF="$(stack_output MigrationTaskDefinitionArn)"
SG_ID="$(stack_output MigrationSecurityGroupId)"
SUBNETS="$(stack_output PrivateSubnetIds)"
for v in CLUSTER TASK_DEF SG_ID SUBNETS; do
  [[ -z "${!v}" || "${!v}" == "None" ]] && { echo "stack output for ${v} missing" >&2; exit 1; }
done

TASK_ROLE_NAME="$(aws_ ecs describe-task-definition --task-definition "${TASK_DEF}" \
  --query 'taskDefinition.taskRoleArn' --output text)"
TASK_ROLE_NAME="${TASK_ROLE_NAME##*/}"
CONTAINER_NAME="$(aws_ ecs describe-task-definition --task-definition "${TASK_DEF}" \
  --query 'taskDefinition.containerDefinitions[0].name' --output text)"

DB_ID="$(aws_ rds describe-db-instances \
  --query "DBInstances[?starts_with(DBInstanceIdentifier, '${STACK_NAME}-database')].DBInstanceIdentifier | [0]" \
  --output text)"
DB_HOST="$(aws_ rds describe-db-instances --db-instance-identifier "${DB_ID}" \
  --query 'DBInstances[0].Endpoint.Address' --output text)"
MASTER_SECRET_ID="nex-health/${STACK_NAME#nex-health-}/database-master"

cat <<EOF
About to dump PRODUCTION (read-only):
  stack        ${STACK_NAME}
  cluster      ${CLUSTER}
  db instance  ${DB_ID}
  task def     ${TASK_DEF##*/}   (command overridden to: sleep 3600)
  task role    ${TASK_ROLE_NAME}   (+ temporary inline policy ${POLICY_NAME})
  output       ${OUT_DIR}
EOF
if [[ "${CONFIRM:-}" != "dump production" ]]; then
  read -r -p "Type 'dump production' to continue: " CONFIRM
fi
[[ "${CONFIRM}" == "dump production" ]] || { echo "aborted"; exit 1; }

TASK_ARN=""
TUNNEL_PID=""
POLICY_ADDED=0
cleanup() {
  set +e
  log "cleaning up"
  if [[ -n "${TUNNEL_PID}" ]]; then
    kill "${TUNNEL_PID}" 2>/dev/null
    wait "${TUNNEL_PID}" 2>/dev/null
  fi
  if [[ -n "${TASK_ARN}" ]]; then
    aws_ ecs stop-task --cluster "${CLUSTER}" --task "${TASK_ARN}" \
      --reason "prod db dump finished" >/dev/null && log "stopped task ${TASK_ARN##*/}"
  fi
  if [[ "${POLICY_ADDED}" == 1 ]]; then
    aws_ iam delete-role-policy --role-name "${TASK_ROLE_NAME}" --policy-name "${POLICY_NAME}" \
      && log "removed temporary policy ${POLICY_NAME}"
  fi
}
trap cleanup EXIT INT TERM

log "adding temporary ECS Exec permissions to ${TASK_ROLE_NAME}"
aws_ iam put-role-policy --role-name "${TASK_ROLE_NAME}" --policy-name "${POLICY_NAME}" \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["ssmmessages:CreateControlChannel","ssmmessages:CreateDataChannel","ssmmessages:OpenControlChannel","ssmmessages:OpenDataChannel"],"Resource":"*"}]}'
POLICY_ADDED=1
sleep 15  # IAM propagation before the task's SSM agent registers

SUBNETS_JSON="$(jq -cn --arg s "${SUBNETS}" '$s | split(",")')"
log "starting one-off tunnel task (sleep only)"
TASK_ARN="$(aws_ ecs run-task \
  --cluster "${CLUSTER}" --launch-type FARGATE --task-definition "${TASK_DEF}" \
  --enable-execute-command --started-by prod-db-dump \
  --network-configuration "awsvpcConfiguration={subnets=${SUBNETS_JSON},securityGroups=[\"${SG_ID}\"],assignPublicIp=DISABLED}" \
  --overrides "$(jq -cn --arg n "${CONTAINER_NAME}" '{containerOverrides:[{name:$n,command:["sleep","3600"]}]}')" \
  --query 'tasks[0].taskArn' --output text)"
[[ -z "${TASK_ARN}" || "${TASK_ARN}" == "None" ]] && { TASK_ARN=""; echo "run-task failed" >&2; exit 1; }
log "task ${TASK_ARN##*/} starting"
aws_ ecs wait tasks-running --cluster "${CLUSTER}" --tasks "${TASK_ARN}"

AGENT_STATE=""
for _ in $(seq 1 40); do
  AGENT_STATE="$(aws_ ecs describe-tasks --cluster "${CLUSTER}" --tasks "${TASK_ARN}" \
    --query "tasks[0].containers[0].managedAgents[?name=='ExecuteCommandAgent'].lastStatus | [0]" --output text)"
  [[ "${AGENT_STATE}" == "RUNNING" ]] && break
  sleep 3
done
[[ "${AGENT_STATE}" == "RUNNING" ]] || { echo "ECS Exec agent not RUNNING (${AGENT_STATE})" >&2; exit 1; }
RUNTIME_ID="$(aws_ ecs describe-tasks --cluster "${CLUSTER}" --tasks "${TASK_ARN}" \
  --query 'tasks[0].containers[0].runtimeId' --output text)"

log "opening tunnel 127.0.0.1:${LOCAL_PORT} -> database:5432"
aws_ ssm start-session \
  --target "ecs:${CLUSTER}_${TASK_ARN##*/}_${RUNTIME_ID}" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "{\"host\":[\"${DB_HOST}\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"${LOCAL_PORT}\"]}" \
  >/dev/null 2>&1 &
TUNNEL_PID=$!
TUNNEL_UP=0
for _ in $(seq 1 30); do
  if (exec 3<>"/dev/tcp/127.0.0.1/${LOCAL_PORT}") 2>/dev/null; then TUNNEL_UP=1; break; fi
  sleep 1
done
[[ "${TUNNEL_UP}" == 1 ]] || { echo "tunnel did not open" >&2; exit 1; }

SECRET_JSON="$(aws_ secretsmanager get-secret-value --secret-id "${MASTER_SECRET_ID}" \
  --query SecretString --output text)"
PGUSER="$(jq -r .username <<<"${SECRET_JSON}")"
PGPASSWORD="$(jq -r .password <<<"${SECRET_JSON}")"
unset SECRET_JSON
export PGUSER PGPASSWORD

umask 077
mkdir -p "${OUT_DIR}"
chmod 700 "${OUT_ROOT}" "${OUT_DIR}"

pg() {  # PostgreSQL 16.14 client against the tunnel; creds passed by env name only
  docker run --rm -i --network host \
    -e PGHOST=127.0.0.1 -e PGPORT="${LOCAL_PORT}" -e PGUSER -e PGPASSWORD \
    -e PGDATABASE="${DB_NAME}" -e PGSSLMODE=require \
    "${PG_IMAGE}" "$@"
}

log "checking connection"
pg psql -Atc "select 'connected to ' || current_database() || ' as ' || current_user"

log "writing manifest"
{
  echo "dumped_at_utc: ${STAMP}"
  echo "db_instance: ${DB_ID}"
  echo "--- alembic_version"
  pg psql -Atc "select version_num from alembic_version"
  echo "--- server version"
  pg psql -Atc "show server_version"
  echo "--- role attributes"
  pg psql -Atc "select rolname, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls from pg_roles where rolname in ('nexhealth_admin','nexhealth_app','app_rls_definer') order by 1"
  echo "--- public schema ACL"
  pg psql -Atc "select nspacl from pg_namespace where nspname = 'public'"
  echo "--- duplicate NexHealth mappings (20260904_nh_mapping_security needs zero rows)"
  pg psql -Atc "select nexhealth_subdomain, nexhealth_location_id, count(*) from institution_locations where nexhealth_subdomain is not null and nexhealth_location_id is not null group by 1,2 having count(*) > 1"
  echo "--- location timezones"
  pg psql -Atc "select coalesce(timezone,'<null>'), count(*) from institution_locations group by 1 order by 2 desc"
  echo "--- extensions"
  pg psql -Atc "select extname || ' ' || extversion from pg_extension order by 1"
  echo "--- tables: estimated rows | total size"
  pg psql -Atc "select relname || ' | ' || n_live_tup || ' | ' || pg_size_pretty(pg_total_relation_size(relid)) from pg_stat_user_tables order by pg_total_relation_size(relid) desc"
} > "${OUT_DIR}/manifest.txt"

log "dumping roles (no passwords)"
pg pg_dumpall --roles-only --no-role-passwords > "${OUT_DIR}/roles.sql"

log "dumping database ${DB_NAME}"
pg pg_dump --format=custom --compress=6 --no-subscriptions --verbose "${DB_NAME}" \
  > "${OUT_DIR}/prod.dump" 2> "${OUT_DIR}/pg_dump.log"
unset PGPASSWORD

log "verifying dump is readable"
docker run --rm -i "${PG_IMAGE}" pg_restore --list < "${OUT_DIR}/prod.dump" > /dev/null

(cd "${OUT_DIR}" && sha256sum prod.dump roles.sql manifest.txt > SHA256SUMS)
chmod 600 "${OUT_DIR}"/*

log "done: ${OUT_DIR}"
ls -lh "${OUT_DIR}"
echo "production alembic revision: $(sed -n '/--- alembic_version/{n;p}' "${OUT_DIR}/manifest.txt")"
