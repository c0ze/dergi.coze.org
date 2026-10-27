# Shared by the scripts in this folder (zsh): the titles on the site, and the issues they have.
SITE_SLUGS=(c64-turkiye commodore 64ler amiga-dunyasi amiga-dergisi megamiga)
PY=.venv/bin/python
ASSET_BASE=https://img.dergi.coze.org/i

# issues_of <slug> [...] → "slug/NN" for every issue directory of those titles
issues_of() { local s d; for s in "$@"; do for d in data/issues/$s/*(N/); do print -r -- "$s/${d:t}"; done; done }
