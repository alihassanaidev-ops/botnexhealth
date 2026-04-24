#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: bash scripts/invite_super_admin_aws.sh <email>"
    exit 1
fi

SUPER_ADMIN_EMAIL="$1"

AWS_PROFILE_NAME="${AWS_PROFILE:-deployer}"
STACK_NAME="${CDK_STACK_NAME:-nex-health-staging}"

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
FRONTEND_URL="$(stack_output FrontendUrl)"

IFS=',' read -r -a SUBNET_ARRAY <<< "${PRIVATE_SUBNET_IDS}"
SUBNETS_JSON=""
for subnet_id in "${SUBNET_ARRAY[@]}"; do
  if [[ -n "${SUBNETS_JSON}" ]]; then
    SUBNETS_JSON+=","
  fi
  SUBNETS_JSON+="\"${subnet_id}\""
done

NETWORK_CONFIGURATION="awsvpcConfiguration={subnets=[${SUBNETS_JSON}],securityGroups=[\"${APP_SECURITY_GROUP_ID}\"],assignPublicIp=\"DISABLED\"}"

echo "Generating secure invite for ${SUPER_ADMIN_EMAIL} on cluster ${CLUSTER_NAME}..."

OVERRIDES=$(cat <<EOF
{
  "containerOverrides": [
    {
      "name": "MigrationContainer",
      "command": [
        "python",
        "-m",
        "src.app.scripts.invite_super_admin",
        "${SUPER_ADMIN_EMAIL}",
        "${FRONTEND_URL}"
      ]
    }
  ]
}
EOF
)

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
  echo "Failed to start Fargate task to create the invite."
  exit 1
fi

echo "Task launched successfully. Waiting for completion... (This will take ~1 minute)"
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

# Grab the Task ID from the ARN to fetch logs
TASK_ID=$(echo "${TASK_ARN}" | awk -F/ '{print $NF}')
LOG_GROUP_PREFIX=$(aws cloudformation describe-stacks --profile "${AWS_PROFILE_NAME}" --stack-name "${STACK_NAME}" --query "Stacks[0].Outputs[?OutputKey=='MigrationTaskDefinitionArn'].OutputValue | [0]" --output text | grep -o 'MigrationTaskDefinition[A-Za-z0-9]*')

# Find the exact log group name dynamically
LOG_GROUP=$(aws logs describe-log-groups --profile "${AWS_PROFILE_NAME}" --query "logGroups[?contains(logGroupName, 'MigrationTaskDefinition')].logGroupName" --output text | awk '{print $1}')
LOG_STREAM="migrations/MigrationContainer/${TASK_ID}"

echo ""
echo "=== SECURE INVITE RESULTS ==="

# We need to fetch the logs to show the generated token URL
aws logs get-log-events \
  --profile "${AWS_PROFILE_NAME}" \
  --log-group-name "${LOG_GROUP}" \
  --log-stream-name "${LOG_STREAM}" \
  --query 'events[].message' \
  --output text

echo "============================="

if [[ "${EXIT_CODE}" != "0" ]]; then
  echo "Task failed with exit code ${EXIT_CODE}."
  exit 1
fi
