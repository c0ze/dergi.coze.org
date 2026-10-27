#!/bin/zsh
# Put new titles on the site: best scan per issue, then normalize → OCR → JEV → assemble →
# correct (batch.py), Apple Vision for display titles on scans, re-labelling of the pages that
# changed, tags. Add the slugs to SITE_SLUGS in scripts/common.sh first, then run
# scripts/refresh-site.sh and scripts/deploy.sh.
# Heavy: ~/.local/bin/heavy-slot nice -n 10 scripts/add-titles.sh <slug> [...]
set -e
cd "${0:A:h}/.."
source scripts/common.sh
(( $# )) || { print -u2 "usage: add-titles.sh <slug> [...]"; exit 2 }
for s in "$@"; do
  $PY pipeline/sources.py $s --build
  $PY pipeline/batch.py $s data/sources/$s/best/*.pdf --jobs 4
done
issues=($(issues_of "$@"))
$PY pipeline/display.py $issues
$PY pipeline/classify.py $issues --jobs 4
$PY pipeline/assemble.py $issues
$PY pipeline/correct.py $issues --jobs 4
$PY pipeline/tags.py $issues --jobs 4
echo "done $(date +%H:%M)"
