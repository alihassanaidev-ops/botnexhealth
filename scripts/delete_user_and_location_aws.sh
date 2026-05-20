#!/usr/bin/env bash
# Soft-delete a user by email and hard-delete a stale InstitutionLocation
# on the deployed staging stack. Uses inline Python so it works against the
# currently-deployed migration image without needing a redeploy.
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: bash scripts/delete_user_and_location_aws.sh <email>[,<email>...] [--location-id <id>] [--dry-run]"
    exit 1
fi

EMAIL="$1"
shift

LOCATION_ID=""
DRY_RUN="false"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --location-id)
            LOCATION_ID="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN="true"
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            exit 1
            ;;
    esac
done

AWS_PROFILE_NAME="${AWS_PROFILE:-deployer}"
STACK_NAME="${CDK_STACK_NAME:-nex-health-staging}"
LOG_GROUP_NAME="${MIGRATION_LOG_GROUP:-/nex-health/staging/migrations}"

stack_output() {
  local key="$1"
  aws cloudformation describe-stacks \
    --profile "${AWS_PROFILE_NAME}" \
    --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue | [0]" \
    --output text
}

CLUSTER_NAME="$(stack_output ClusterName)"
TASK_DEFINITION_ARN="$(stack_output MigrationTaskDefinitionArn)"
APP_SECURITY_GROUP_ID="$(stack_output AppSecurityGroupId)"
PRIVATE_SUBNET_IDS="$(stack_output PrivateSubnetIds)"

IFS=',' read -r -a SUBNET_ARRAY <<< "${PRIVATE_SUBNET_IDS}"
SUBNETS_JSON=""
for subnet_id in "${SUBNET_ARRAY[@]}"; do
  if [[ -n "${SUBNETS_JSON}" ]]; then
    SUBNETS_JSON+=","
  fi
  SUBNETS_JSON+="\"${subnet_id}\""
done

NETWORK_CONFIGURATION="awsvpcConfiguration={subnets=[${SUBNETS_JSON}],securityGroups=[\"${APP_SECURITY_GROUP_ID}\"],assignPublicIp=\"DISABLED\"}"

# Inline Python: soft-deletes the user, NULLs user.location_id, and
# hard-deletes the stale location. Idempotent if the user is already
# soft-deleted or the location is already gone.
read -r -d '' PY_SCRIPT <<'PYEOF' || true
import asyncio
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from src.app.config import settings
from src.app.database import create_async_engine
from src.app.models.user import User
from src.app.models.institution_location import InstitutionLocation


EMAILS = [e.strip().lower() for e in os.environ["TARGET_EMAIL"].split(",") if e.strip()]
LOCATION_ID_OVERRIDE = os.environ.get("TARGET_LOCATION_ID") or None
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


async def main() -> None:
    if not settings.database_url:
        print("DATABASE_URL is not set", flush=True)
        sys.exit(1)

    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with async_session() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.context_type', 'user', false), "
                    "set_config('app.role', 'SUPER_ADMIN', false), "
                    "set_config('app.user_id', :uid, false)"
                ),
                {"uid": "00000000-0000-0000-0000-000000000000"},
            )

            users: list[User] = []
            for email in EMAILS:
                u = (
                    await session.execute(
                        select(User).where(User.email == email, User.deleted_at.is_(None))
                    )
                ).scalar_one_or_none()
                if u is None:
                    print(f"No active user with email {email}", flush=True)
                    sys.exit(1)
                users.append(u)

            resolved_location_id = LOCATION_ID_OVERRIDE or users[0].location_id
            if resolved_location_id is None:
                print(
                    f"User {EMAILS[0]} has no location_id; set TARGET_LOCATION_ID env",
                    flush=True,
                )
                sys.exit(1)

            # Sanity: every targeted user must currently point at the same location
            for u in users:
                if u.location_id != resolved_location_id:
                    print(
                        f"User {u.email} location_id={u.location_id} does not match "
                        f"target {resolved_location_id}; aborting",
                        flush=True,
                    )
                    sys.exit(1)

            # Catch any remaining active users that reference this location
            other_users = (
                await session.execute(
                    select(User).where(
                        User.location_id == resolved_location_id,
                        User.deleted_at.is_(None),
                        ~User.email.in_(EMAILS),
                    )
                )
            ).scalars().all()
            if other_users:
                print(
                    f"Location {resolved_location_id} still referenced by "
                    f"{len(other_users)} other active user(s):",
                    flush=True,
                )
                for u in other_users:
                    print(f"  {u.email} ({u.id})", flush=True)
                print("Aborting — handle these users first.", flush=True)
                sys.exit(1)

            location_row = (
                await session.execute(
                    select(InstitutionLocation).where(
                        InstitutionLocation.id == resolved_location_id
                    )
                )
            ).scalar_one_or_none()

            print("=" * 70, flush=True)
            print("PLAN", flush=True)
            for u in users:
                print(
                    f"  user: id={u.id} email={u.email} role={u.role} "
                    f"invite_status={u.invite_status}",
                    flush=True,
                )
            print(f"  target location  = {resolved_location_id}", flush=True)
            if location_row is not None:
                print(f"  location.name    = {location_row.name!r}", flush=True)
                print(f"  location.inst    = {location_row.institution_id}", flush=True)
            else:
                print("  location.name    = (not found)", flush=True)
            print("=" * 70, flush=True)

            if DRY_RUN:
                print("DRY RUN — no changes made.", flush=True)
                return

            now = datetime.now(timezone.utc)
            for u in users:
                u.deleted_at = now
                u.location_id = None
                u.is_active = False
            await session.flush()

            if location_row is not None:
                await session.delete(location_row)
                await session.flush()

            await session.commit()

            print("DONE", flush=True)
            for u in users:
                print(f"  soft-deleted user {u.email} (id={u.id})", flush=True)
            if location_row is not None:
                print(f"  hard-deleted location id={resolved_location_id}", flush=True)
            print("Emails are free to re-invite.", flush=True)
    finally:
        await engine.dispose()


asyncio.run(main())
PYEOF

ENCODED_SCRIPT="$(printf '%s' "${PY_SCRIPT}" | base64 | tr -d '\n')"

PY_INVOCATION="import base64; exec(base64.b64decode('${ENCODED_SCRIPT}').decode())"

ENV_OVERRIDES="{\"name\":\"TARGET_EMAIL\",\"value\":\"${EMAIL}\"},{\"name\":\"DRY_RUN\",\"value\":\"${DRY_RUN}\"}"
if [[ -n "${LOCATION_ID}" ]]; then
  ENV_OVERRIDES+=",{\"name\":\"TARGET_LOCATION_ID\",\"value\":\"${LOCATION_ID}\"}"
fi

OVERRIDES=$(cat <<EOF
{
  "containerOverrides": [
    {
      "name": "MigrationContainer",
      "command": ["python", "-c", "${PY_INVOCATION}"],
      "environment": [${ENV_OVERRIDES}]
    }
  ]
}
EOF
)

echo "Running on cluster ${CLUSTER_NAME} (stack ${STACK_NAME}, dry_run=${DRY_RUN})..."

TASK_ARN="$(
  aws ecs run-task \
    --profile "${AWS_PROFILE_NAME}" \
    --cluster "${CLUSTER_NAME}" \
    --launch-type FARGATE \
    --task-definition "${TASK_DEFINITION_ARN}" \
    --network-configuration "${NETWORK_CONFIGURATION}" \
    --overrides "${OVERRIDES}" \
    --query 'tasks[0].taskArn' \
    --output text
)"

if [[ "${TASK_ARN}" == "None" || -z "${TASK_ARN}" ]]; then
  echo "Failed to start Fargate task."
  exit 1
fi

TASK_ID="$(echo "${TASK_ARN}" | awk -F/ '{print $NF}')"
echo "Task launched: ${TASK_ID}. Waiting for completion..."

aws ecs wait tasks-stopped \
  --profile "${AWS_PROFILE_NAME}" \
  --cluster "${CLUSTER_NAME}" \
  --tasks "${TASK_ARN}"

EXIT_CODE="$(
  aws ecs describe-tasks \
    --profile "${AWS_PROFILE_NAME}" \
    --cluster "${CLUSTER_NAME}" \
    --tasks "${TASK_ARN}" \
    --query 'tasks[0].containers[0].exitCode' \
    --output text
)"

LOG_STREAM="migrations/MigrationContainer/${TASK_ID}"

echo ""
echo "=== TASK OUTPUT ==="
aws logs get-log-events \
  --profile "${AWS_PROFILE_NAME}" \
  --log-group-name "${LOG_GROUP_NAME}" \
  --log-stream-name "${LOG_STREAM}" \
  --query 'events[].message' \
  --output text || true
echo "==================="

if [[ "${EXIT_CODE}" != "0" ]]; then
  echo "Task failed with exit code ${EXIT_CODE}."
  exit 1
fi
