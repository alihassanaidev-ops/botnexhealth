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

FRONTEND_BUCKET_NAME="$(stack_output FrontendBucketName)"
FRONTEND_DISTRIBUTION_ID="$(stack_output FrontendDistributionId)"
FRONTEND_URL="$(stack_output FrontendUrl)"

if [[ -z "${FRONTEND_BUCKET_NAME}" || "${FRONTEND_BUCKET_NAME}" == "None" ]]; then
  echo "FrontendBucketName output is missing from stack ${STACK_NAME}."
  exit 1
fi

if [[ -z "${FRONTEND_DISTRIBUTION_ID}" || "${FRONTEND_DISTRIBUTION_ID}" == "None" ]]; then
  echo "FrontendDistributionId output is missing from stack ${STACK_NAME}."
  exit 1
fi

echo "Building frontend against ${FRONTEND_URL}/api ..."
(
  cd nexus-dashboard-web
  # Overwrite local .env to ensure Vite picks up the correct URL for the build
  echo "VITE_API_URL=${FRONTEND_URL}/api" > .env.production
  npm run build -- --mode production
)

echo "Syncing frontend assets to s3://${FRONTEND_BUCKET_NAME} ..."
aws s3 sync \
  --profile "${AWS_PROFILE_NAME}" \
  nexus-dashboard-web/dist \
  "s3://${FRONTEND_BUCKET_NAME}/" \
  --delete

echo "Invalidating CloudFront distribution ${FRONTEND_DISTRIBUTION_ID} ..."
aws cloudfront create-invalidation \
  --profile "${AWS_PROFILE_NAME}" \
  --distribution-id "${FRONTEND_DISTRIBUTION_ID}" \
  --paths '/*'
