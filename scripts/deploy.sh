#!/usr/bin/env bash
set -euo pipefail

PROJECT="work-dashboards"
REGION="asia-southeast1"
SERVICE="family-expenses"
SECRET="family-expenses-database-url"
SERVICE_ACCOUNT="family-expenses@${PROJECT}.iam.gserviceaccount.com"

DRY_RUN=0
ALLOW_DIRTY=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --allow-dirty) ALLOW_DIRTY=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [[ "$ALLOW_DIRTY" -ne 1 ]] && [[ -n "$(git status --porcelain)" ]]; then
  echo "refusing to deploy a dirty working tree (use --allow-dirty only for dry-run inspection)" >&2
  exit 1
fi

SHA="$(git rev-parse HEAD)"
if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "could not resolve a full Git commit SHA" >&2
  exit 1
fi
IMAGE="gcr.io/${PROJECT}/${SERVICE}:${SHA}"

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '%q ' "$@"
    printf '\n'
  else
    "$@"
  fi
}

# Auth cutover is deliberate. Preserve ALL existing portal settings and secret
# bindings; --set-* would remove unrelated configuration. Never rotate sessions.
MCP_AUTH_ISSUER="${MCP_AUTH_ISSUER:-https://work-os.jp.auth0.com/}"
MCP_RESOURCE_URL="${MCP_RESOURCE_URL:-https://family-expenses-bejtu5m47a-as.a.run.app/mcp}"
MCP_MEMBERS_SECRET="${MCP_MEMBERS_SECRET:-family-expenses-mcp-members}"
MCP_MEMBERS_SECRET_VERSION="${MCP_MEMBERS_SECRET_VERSION:-}"
if [[ -z "$MCP_MEMBERS_SECRET_VERSION" ]]; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    MCP_MEMBERS_SECRET_VERSION="APPROVED_VERSION_REQUIRED"
  else
    echo "MCP_MEMBERS_SECRET_VERSION must be an approved pinned numeric version" >&2
    exit 1
  fi
fi
if [[ "$DRY_RUN" -ne 1 ]] && [[ ! "$MCP_MEMBERS_SECRET_VERSION" =~ ^[0-9]+$ ]]; then
  echo "MCP member policy requires a numeric Secret Manager version" >&2
  exit 1
fi

run gcloud builds submit . \
  --config=cloudbuild.yaml \
  --project="$PROJECT" \
  --substitutions="COMMIT_SHA=${SHA}"

run gcloud run deploy "$SERVICE" \
  --project="$PROJECT" \
  --region="$REGION" \
  --image="$IMAGE" \
  --service-account="$SERVICE_ACCOUNT" \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=3 \
  --update-env-vars="^~^MCP_AUTH_ISSUER=${MCP_AUTH_ISSUER}~MCP_RESOURCE_URL=${MCP_RESOURCE_URL}" \
  --update-secrets="MCP_MEMBERS_JSON=${MCP_MEMBERS_SECRET}:${MCP_MEMBERS_SECRET_VERSION}" \
  --quiet

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "SERVICE_URL=<resolved after deploy>"
else
  SERVICE_URL="$(gcloud run services describe "$SERVICE" \
    --project="$PROJECT" --region="$REGION" --format='value(status.url)')"
  if [[ -z "$SERVICE_URL" ]]; then
    echo "deployment completed but service URL was empty" >&2
    exit 1
  fi
  echo "SERVICE_URL=${SERVICE_URL}"
fi
