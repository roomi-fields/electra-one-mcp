#!/usr/bin/env bash
# Refresh the local mirror of docs.electra.one and rebuild the structured
# distillation. Re-running is idempotent — already-downloaded pages are
# skipped unless they're missing.
#
# Run from the plugin root.
set -euo pipefail

ROOT="docs"
mkdir -p "$ROOT/md" "$ROOT/structured"

# Seed list — pages we know exist. The crawl will discover the rest.
SEEDS=(
  "/"
  "/developers/luaext.html"
  "/developers/presetformat.html"
  "/developers/midiimplementation.html"
  "/developers/instrumentformat.html"
  "/developers/confformat.html"
  "/developers/devicesformat.html"
  "/developers/performanceformat.html"
  "/developers/filetransfer.html"
  "/userguide/quickstart.html"
  "/userguide/editor.html"
  "/userguide/firstpreset.html"
  "/luacourse/index.html"
  "/troubleshooting/index.html"
  "/downloads/firmware.html"
  "/downloads/updatemkII.html"
)

declare -A SEEN
QUEUE=("${SEEDS[@]}")
DISCOVERED=()

while [ ${#QUEUE[@]} -gt 0 ]; do
  url="${QUEUE[0]}"
  QUEUE=("${QUEUE[@]:1}")
  [ -n "${SEEN[$url]:-}" ] && continue
  SEEN[$url]=1
  DISCOVERED+=("$url")
  content=$(curl -sS "https://docs.electra.one${url}" 2>/dev/null) || continue
  [ -z "$content" ] && continue
  while IFS= read -r link; do
    clean=$(echo "$link" | sed 's/[?#].*//')
    if [[ "$clean" == /* ]] && [[ "$clean" == *.html ]] && [ -z "${SEEN[$clean]:-}" ]; then
      QUEUE+=("$clean")
    fi
  done < <(echo "$content" | grep -oE 'href="[^"]*\.html[^"]*"' | sed 's/href="//; s/".*$//')
done

echo "Discovered ${#DISCOVERED[@]} pages"

if ! command -v html2text >/dev/null; then
  echo "warning: html2text not installed — skipping markdown extraction"
  echo "         install with:  sudo apt install html2text  (Debian/Ubuntu)"
  echo "                       brew install html2text       (macOS)"
fi

for url in "${DISCOVERED[@]}"; do
  path="${url#/}"
  [ -z "$path" ] && path="index.html"
  out_md="$ROOT/md/${path%.html}.md"
  mkdir -p "$(dirname "$out_md")"
  if [ -s "$out_md" ]; then continue; fi
  curl -sS "https://docs.electra.one${url}" | python3 -c "
import re, sys
html = sys.stdin.read()
m = re.search(r'<main[^>]*>(.*?)</main>', html, re.DOTALL)
body = m.group(1) if m else html
body = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.DOTALL)
body = re.sub(r'<style[^>]*>.*?</style>', '', body, flags=re.DOTALL)
sys.stdout.write(body)
" | (html2text -nobs -utf8 2>/dev/null || cat) > "$out_md"
done

echo "Markdown saved to $ROOT/md/ ($(find "$ROOT/md" -name '*.md' | wc -l) files)"

# Rebuild structured distillation
if [ -f "scripts/build-structured.py" ]; then
  echo "Rebuilding structured docs…"
  python3 scripts/build-structured.py
fi
