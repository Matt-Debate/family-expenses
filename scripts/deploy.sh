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

# Portal OAuth (Auth0 Universal Login). MCP_SECRET is still never set — /mcp
# stays open and header-free; this gates the PORTAL only. Clear
# PORTAL_ALLOWED_EMAILS to lock everyone out, or drop the AUTH0_* vars entirely
# to turn login off and return to the pre-0.5.0 behavior.
AUTH0_DOMAIN="${AUTH0_DOMAIN:-work-os.jp.auth0.com}"
AUTH0_CLIENT_ID="${AUTH0_CLIENT_ID:-EA00YyDGHHz7ATEguoAIWmSq6xD8iOES}"
AUTH0_SECRET="${AUTH0_SECRET:-family-expenses-auth0-client-secret}"
SESSION_SECRET_NAME="${SESSION_SECRET_NAME:-family-expenses-session-secret}"
PORTAL_ALLOWED_EMAILS="${PORTAL_ALLOWED_EMAILS:-matthewfarm@gmail.com}"
PORTAL_BASE_URL="${PORTAL_BASE_URL:-https://family-expenses-bejtu5m47a-as.a.run.app}"

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
  --set-env-vars="HOST=0.0.0.0,APP_TZ=Asia/Shanghai,AUTH0_DOMAIN=${AUTH0_DOMAIN},AUTH0_CLIENT_ID=${AUTH0_CLIENT_ID},PORTAL_ALLOWED_EMAILS=${PORTAL_ALLOWED_EMAILS},PORTAL_BASE_URL=${PORTAL_BASE_URL}" \
  --set-secrets="DATABASE_URL=${SECRET}:latest,AUTH0_CLIENT_SECRET=${AUTH0_SECRET}:latest,SESSION_SECRET=${SESSION_SECRET_NAME}:latest" \
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
