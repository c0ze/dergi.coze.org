#!/bin/sh
# Deploy the search API to Cloud Run (us-central1) with the latest passage vectors.
# Uses the gcloud configuration `personal`; the build runs in Cloud Build.
set -eu
cd "$(dirname "$0")"
mkdir -p data
cp ../data/search/vectors.npy ../data/search/passages.json data/
gcloud --configuration=personal run deploy dergi-api \
  --project=dergi-coze-org --region=us-central1 --source=. \
  --service-account=dergi-api@dergi-coze-org.iam.gserviceaccount.com \
  --allow-unauthenticated --min-instances=0 --max-instances=2 \
  --memory=512Mi --cpu=1 --concurrency=40 --timeout=20 --quiet
