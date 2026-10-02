#!/usr/bin/env bash
#
# Publish the frontend from an arbitrary git ref, without disturbing the
# working tree.
#
# The frontend is static files on S3 behind CloudFront, published entirely
# separately from the API — so putting a different build live is a two-minute
# operation that never touches ECS, never pushes an image, and cannot affect
# the backend. That makes "switch the UI back" cheap enough to decide late.
#
#   scripts/publish_frontend_ref.sh <git-ref>
#
# e.g. scripts/publish_frontend_ref.sh ui-baseline-2026-09-05
#      scripts/publish_frontend_ref.sh origin/staging
set -euo pipefail

REF="${1:-}"
if [[ -z "${REF}" ]]; then
  echo "usage: $0 <git-ref>" >&2
  exit 64
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "${REPO_ROOT}"

if ! git rev-parse --verify --quiet "${REF}^{commit}" >/dev/null; then
  echo "Not a commit: ${REF}" >&2
  exit 64
fi

RESOLVED="$(git rev-parse --short "${REF}")"
WORKTREE="$(mktemp -d "${TMPDIR:-/tmp}/nexus-frontend-XXXXXX")"
cleanup() { git worktree remove --force "${WORKTREE}" 2>/dev/null || true; }
trap cleanup EXIT

echo "Checking out ${REF} (${RESOLVED}) into a scratch worktree ..."
git worktree add --detach --quiet "${WORKTREE}" "${REF}"

# Reuse the main checkout's dependencies when the lockfile is byte-identical.
# A fresh `npm ci` here would cost minutes and a large download for a build
# whose inputs have not changed; if the lockfile differs at all, fall back to
# installing rather than guessing.
if cmp -s "nexus-dashboard-web/package-lock.json" "${WORKTREE}/nexus-dashboard-web/package-lock.json" \
   && [[ -d "nexus-dashboard-web/node_modules" ]]; then
  echo "Lockfile matches — reusing existing node_modules."
  ln -s "${REPO_ROOT}/nexus-dashboard-web/node_modules" "${WORKTREE}/nexus-dashboard-web/node_modules"
else
  echo "Lockfile differs from the working tree — installing dependencies."
  (cd "${WORKTREE}/nexus-dashboard-web" && npm ci)
fi

echo "Publishing ..."
(cd "${WORKTREE}" && AWS_PROFILE="${AWS_PROFILE:-deployer}" \
  CDK_STACK_NAME="${CDK_STACK_NAME:-nex-health-staging}" \
  bash scripts/publish_frontend_from_cdk.sh)

echo
echo "Published ${REF} (${RESOLVED}) to ${CDK_STACK_NAME:-nex-health-staging}."
echo "CloudFront invalidation takes a minute or two to finish."
