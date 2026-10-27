#!/bin/sh
# Deploy the search API and MCP server to Cloud Run (us-central1) with the latest passage vectors
# and catalogue (run pipeline/export_api.py first).
# Uses the gcloud configuration `personal`; the build runs in Cloud Build.
set -eu
cd "$(dirname "$0")"
mkdir -p data
cp ../data/search/vectors.npy ../data/search/passages.json ../data/search/catalog.json.gz data/
gcloud --configuration=personal run deploy dergi-api \
  --project=dergi-coze-org --region=us-central1 --source=. \
  --service-account=dergi-api@dergi-coze-org.iam.gserviceaccount.com \
  --allow-unauthenticated --min-instances=0 --max-instances=2 \
  --memory=1Gi --cpu=1 --concurrency=40 --timeout=20 --quiet
