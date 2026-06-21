#!/bin/bash
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT_DIR="$CLAUDE_PROJECT_DIR"
JAR_TARGET_DIR="$ROOT_DIR/java/opendataloader-pdf-cli/target"
JAR_PATH="$JAR_TARGET_DIR/opendataloader-pdf-cli-0.0.0.jar"

# Download JAR from published PyPI wheel if not already present.
# Building from source requires the vera-dev Artifactory host which is
# blocked in this remote environment; the PyPI wheel carries a pre-built JAR.
if [ ! -f "$JAR_PATH" ]; then
  echo "=== Downloading JAR from PyPI wheel ==="
  TMP_DIR=$(mktemp -d)
  pip download opendataloader-pdf --no-deps -q -d "$TMP_DIR"
  WHEEL=$(ls "$TMP_DIR"/*.whl | head -1)
  mkdir -p "$JAR_TARGET_DIR"
  python3 -c "
import zipfile, sys, shutil
whl, dest = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(whl) as z:
    member = next(n for n in z.namelist() if n.endswith('/jar/opendataloader-pdf-cli.jar'))
    with z.open(member) as src, open(dest, 'wb') as dst:
        shutil.copyfileobj(src, dst)
print(f'Extracted JAR to {dest}')
" "$WHEEL" "$JAR_PATH"
  rm -rf "$TMP_DIR"
else
  echo "=== JAR already present, skipping download ==="
fi

echo "=== Installing Python dependencies ==="
cd "$ROOT_DIR/python/opendataloader-pdf"
# hatchling validates [project.readme] before build hooks run, so README must exist
cp "$ROOT_DIR/README.md" "$ROOT_DIR/python/opendataloader-pdf/README.md"
uv sync

echo "=== Installing and building Node.js package ==="
cd "$ROOT_DIR/node/opendataloader-pdf"
pnpm install
pnpm run build

echo "=== Session setup complete ==="
