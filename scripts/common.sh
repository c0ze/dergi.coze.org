# Shared by the scripts in this folder (zsh): the titles on the site, and the issues they have.
SITE_SLUGS=(c64-turkiye commodore 64ler amiga-dunyasi commodore-amiga amiga-dergisi megamiga matematik-dunyasi laneth)
PY=.venv/bin/python
ASSET_BASE=https://img.dergi.coze.org/i

# issues_of <slug> [...] → "slug/NN" for every issue directory of those titles
issues_of() { local s d; for s in "$@"; do for d in data/issues/$s/*(N/); do print -r -- "$s/${d:t}"; done; done }

# image_sync <src> <dst> [gcloud flags]: copy changed images to the bucket and delete what the build
# no longer has (figures a re-assembly renamed or dropped). A partial data/ would delete whole
# issues, so a dry run goes first: any page, thumbnail or program it would remove stops the deploy
# (ALLOW_DELETE=1 goes on).
image_sync() {
  local src=$1 dst=$2; shift 2
  local sync=(gcloud --configuration=personal storage rsync -r --checksums-only --delete-unmatched-destination-objects "$@" $src $dst)
  local plan
  plan=$($sync --dry-run 2>&1) || { print -u2 "image sync dry run failed for $dst:"; print -u2 -r -- "$plan"; return 1 }
  local gone=($(print -r -- "$plan" | tr '\r' '\n' | grep '^Would remove' | grep -v '/figures/' | awk '{print $3}'))
  if (( ${#gone} )) && [[ $ALLOW_DELETE != 1 ]]; then
    print -u2 "image sync would delete ${#gone} pages, thumbnails or programs under $dst (first: $gone[1]); check data/, or ALLOW_DELETE=1"
    return 1
  fi
  $sync
}
