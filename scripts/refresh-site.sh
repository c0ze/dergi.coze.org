#!/bin/zsh
# Re-assemble every site issue after an assembly change, then redo what depends on the text.
# Corrections, tags and embeddings are cached by text, so only changed pieces cost anything.
# Heavy: ~/.local/bin/heavy-slot nice -n 10 scripts/refresh-site.sh
set -e
cd "${0:A:h}/.."
source scripts/common.sh
issues=($(issues_of $SITE_SLUGS))
$PY pipeline/assemble.py $issues > data/refresh-assemble.log
$PY pipeline/correct.py $issues --jobs 4
$PY pipeline/tags.py $issues --jobs 4
$PY pipeline/typeins.py ${(M)issues:#(c64-turkiye|commodore|64ler)/*}
$PY pipeline/taxonomy.py
$PY pipeline/embed.py $SITE_SLUGS
echo "done $(date +%H:%M)"
