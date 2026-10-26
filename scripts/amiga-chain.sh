#!/bin/zsh
# Amiga titles onto the site: best copies → pipeline → tags → embeddings.
# Run through heavy-slot: ~/.local/bin/heavy-slot nice -n 10 scripts/amiga-chain.sh
set -e
cd "${0:A:h}/.."
PY=.venv/bin/python
SLUGS=(amiga-dunyasi amiga-dergisi megamiga)
for s in $SLUGS; do
  $PY pipeline/sources.py $s --build
  $PY pipeline/batch.py $s data/sources/$s/best/*.pdf --jobs 4
done
issues=()
for s in $SLUGS; do for d in data/issues/$s/*(/); do issues+=($s/${d:t}); done; done
$PY pipeline/tags.py $issues --jobs 4
$PY pipeline/taxonomy.py
$PY pipeline/embed.py c64-turkiye commodore 64ler $SLUGS
echo "done $(date +%H:%M)"
