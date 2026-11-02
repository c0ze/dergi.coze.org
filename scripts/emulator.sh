#!/bin/zsh
# The in-browser C64 for the type-in programs: EmulatorJS with its VICE x64 core (x128 for the
# C128 BASIC ones), from npm at a pinned version, into data/emu (build.py publishes it under
# /emu/ on the site itself). Only what a .prg needs: the loader and its sources, Turkish texts,
# the 7z extractor (the cores are 7z archives), the cores (plus their no-WebGL2 builds) and their
# reports. Both are GPL; the page links their source repositories.
set -e
cd "${0:A:h}/.."
VERSION=4.2.3
OUT=data/emu
tmp=$(mktemp -d)
trap 'rm -rf $tmp' EXIT
(cd $tmp && npm pack -q @emulatorjs/emulatorjs@$VERSION @emulatorjs/core-vice_x64@$VERSION \
  @emulatorjs/core-vice_x128@$VERSION >/dev/null)
mkdir -p $tmp/ejs $tmp/core $tmp/core128
tar xzf $tmp/emulatorjs-emulatorjs-$VERSION.tgz -C $tmp/ejs
tar xzf $tmp/emulatorjs-core-vice_x64-$VERSION.tgz -C $tmp/core
tar xzf $tmp/emulatorjs-core-vice_x128-$VERSION.tgz -C $tmp/core128
rm -rf $OUT
mkdir -p $OUT/src $OUT/localization $OUT/compression $OUT/cores/reports
src=$tmp/ejs/package/data
cp $src/loader.js $src/emulator.css $src/version.json $OUT/
cp $src/src/*.js $OUT/src/
cp $src/localization/tr-TR.json $OUT/localization/
cp $src/compression/extract7z.js $OUT/compression/
cp $tmp/ejs/package/LICENSE $OUT/LICENSE
cp $tmp/core/package/vice_x64-wasm.data $tmp/core/package/vice_x64-legacy-wasm.data $OUT/cores/
cp $tmp/core/package/reports/vice_x64.json $OUT/cores/reports/
cp $tmp/core128/package/vice_x128-wasm.data $tmp/core128/package/vice_x128-legacy-wasm.data $OUT/cores/
cp $tmp/core128/package/reports/vice_x128.json $OUT/cores/reports/
du -sh $OUT
