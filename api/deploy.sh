#!/bin/sh
# Deploy the search API and MCP server to Cloud Run (us-central1) with the latest passage vectors
# and catalogue (run pipeline/export_api.py first).
# Uses the gcloud configuration `personal`; the build runs in Cloud Build. Skipped when the code
# and data are those of the last deploy from this checkout (FORCE=1 deploys anyway).
set -eu
cd "$(dirname "$0")"
mkdir -p data
cp ../data/search/vectors.npy ../data/search/passages.json ../data/search/catalog.json.gz data/
STAMP=../data/search/api-deployed.sha256
inputs=$(sha256sum deploy.sh Dockerfile .gcloudignore main.py requirements.txt data/*)
if [ "${FORCE:-}" != 1 ] && [ -f "$STAMP" ] && [ "$inputs" = "$(cat "$STAMP")" ]; then
  echo "dergi-api: code and data unchanged since the last deploy; not redeploying"
  exit 0
fi
gcloud --configuration=personal run deploy dergi-api \
  --project=dergi-coze-org --region=us-central1 --source=. \
  --service-account=dergi-api@dergi-coze-org.iam.gserviceaccount.com \
  --allow-unauthenticated --min-instances=0 --max-instances=2 \
  --memory=1Gi --cpu=1 --concurrency=40 --timeout=20 --quiet
printf "%s\n" "$inputs" > "$STAMP"
