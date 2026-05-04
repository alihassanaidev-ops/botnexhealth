#!/usr/bin/env bash
set -euo pipefail

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
MIGRATION_SECURITY_GROUP_ID="$(stack_output MigrationSecurityGroupId)"
if [[ "${MIGRATION_SECURITY_GROUP_ID}" == "None" || -z "${MIGRATION_SECURITY_GROUP_ID}" ]]; then
  MIGRATION_SECURITY_GROUP_ID="$(stack_output AppSecurityGroupId)"
fi
PRIVATE_SUBNET_IDS="$(stack_output PrivateSubnetIds)"

IFS=',' read -r -a SUBNET_ARRAY <<< "${PRIVATE_SUBNET_IDS}"
SUBNETS_JSON=""
for subnet_id in "${SUBNET_ARRAY[@]}"; do
  if [[ -n "${SUBNETS_JSON}" ]]; then
    SUBNETS_JSON+=","
  fi
  SUBNETS_JSON+="\"${subnet_id}\""
done

NETWORK_CONFIGURATION="awsvpcConfiguration={subnets=[${SUBNETS_JSON}],securityGroups=[\"${MIGRATION_SECURITY_GROUP_ID}\"],assignPublicIp=\"DISABLED\"}"

echo "Running migration task on cluster ${CLUSTER_NAME}..."
TASK_ARN="$(
  aws ecs run-task \
    --profile "${AWS_PROFILE_NAME}" \
    --cluster "${CLUSTER_NAME}" \
    --launch-type FARGATE \
    --task-definition "${TASK_DEFINITION_ARN}" \
    --network-configuration "${NETWORK_CONFIGURATION}" \
    --query 'tasks[0].taskArn' \
    --output text
)"

if [[ "${TASK_ARN}" == "None" || -z "${TASK_ARN}" ]]; then
  echo "Failed to start migration task."
  exit 1
fi

echo "Waiting for task ${TASK_ARN} to stop..."
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
STOP_REASON="$(
  aws ecs describe-tasks \
    --profile "${AWS_PROFILE_NAME}" \
    --cluster "${CLUSTER_NAME}" \
    --tasks "${TASK_ARN}" \
    --query 'tasks[0].stoppedReason' \
    --output text
)"

echo "Task stopped with exit code ${EXIT_CODE}."
echo "Stop reason: ${STOP_REASON}"

if [[ "${EXIT_CODE}" != "0" ]]; then
  exit 1
fi
