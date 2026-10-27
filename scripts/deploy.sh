#!/bin/zsh
# Build and publish everything: static site (Firebase Hosting), images (private bucket behind
# the img.dergi.coze.org Worker), the API catalogue, the search API + MCP server (Cloud Run),
# then check every MCP tool through the public URL.
set -e
cd "${0:A:h}/.."
source scripts/common.sh
$PY site/build.py --only ${(j:,:)SITE_SLUGS} --asset-base $ASSET_BASE
npx -y pagefind@1.3.0 --site data/site
gcloud --configuration=personal storage rsync -r data/site/i gs://dergi-coze-org-site/i --cache-control="public, max-age=86400"
$PY site/deploy.py
$PY pipeline/export_api.py
sh api/deploy.sh
$PY scripts/mcp_smoke.py
