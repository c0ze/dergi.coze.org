#!/bin/zsh
# Build and publish everything: static site (Firebase Hosting), images (private bucket behind
# the img.dergi.coze.org Worker), the API catalogue, the search API + MCP server (Cloud Run),
# then check every MCP tool through the public URL.
set -e
cd "${0:A:h}/.."
source scripts/common.sh
$PY site/build.py --only ${(j:,:)SITE_SLUGS} --asset-base $ASSET_BASE
# Titles marked private (waiting for permission) go only to the private site: stop if any of
# their pages or images is in the public build (scripts/deploy-private.sh publishes those).
for s in $($PY -c 'import yaml; print(" ".join(t["slug"] for t in yaml.safe_load(open("catalog/titles.yaml")) if t.get("private")))'); do
  [[ -e data/site/$s || -e data/site/i/$s ]] && { print -u2 "private title $s in data/site; not deploying"; exit 1 }
done
npx -y pagefind@1.3.0 --site data/site
gcloud --configuration=personal storage rsync -r data/site/i gs://dergi-coze-org-site/i --cache-control="public, max-age=86400"
$PY site/deploy.py
$PY pipeline/export_api.py
sh api/deploy.sh
$PY scripts/mcp_smoke.py
