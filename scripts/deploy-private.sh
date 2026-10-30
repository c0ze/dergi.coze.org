#!/bin/zsh
# Build and publish the private site (ozel.dergi.coze.org, behind Cloudflare Access): every title,
# including those marked `private: true` in catalog/titles.yaml. Pages, search index and static
# files go to gs://dergi-coze-org-site/ozel/site, the private titles' images to ozel/i/<slug>; the
# other titles' images are the public ones under i/ (scripts/deploy.sh). The Worker in workers/img
# serves the hostname and refuses requests without a valid Access token.
set -e
cd "${0:A:h}/.."
source scripts/common.sh
BUCKET=gs://dergi-coze-org-site
private=($($PY -c 'import yaml; print(" ".join(t["slug"] for t in yaml.safe_load(open("catalog/titles.yaml")) if t.get("private")))'))
$PY site/build.py --only ${(j:,:)SITE_SLUGS} --private
npx -y pagefind@1.3.0 --site data/site-private
gcloud --configuration=personal storage rsync -r --delete-unmatched-destination-objects \
  --exclude='^i/.*' data/site-private $BUCKET/ozel/site
for s in $private; do
  [[ -d data/site-private/i/$s ]] || { echo "$s: nothing built yet"; continue }
  gcloud --configuration=personal storage rsync -r data/site-private/i/$s $BUCKET/ozel/i/$s
done
echo "private site: ${#private} private titles ($private) → https://ozel.dergi.coze.org/"
