#!/bin/bash
# Fetch the OCR engine's models into DIR/models, one file after another: PaddleOCR's ONNX exports
# (Apache-2.0, huggingface.co/PaddlePaddle), each at a fixed revision (setup/models.tsv, 221 MB).
# Add setup/models-small.tsv for PP-OCRv6 small (twice as fast, reads Latin worse; 31 MB).
# Resumable: rerun it and it continues (.part files), skipping files already verified. Every file
# is checked: the models by sha256, their small yml files by their git blob sha1.
#
#   setup/fetch.sh [DIR] [MANIFEST ...]      (default /media/shared/AIModels/ocr, setup/models.tsv)
set -u
BASE=${1:-/media/shared/AIModels/ocr}
HERE=$(cd "$(dirname "$0")" && pwd)
shift || true
MANIFESTS=("$@")
[ ${#MANIFESTS[@]} -eq 0 ] && MANIFESTS=("$HERE/models.tsv")

verify() { # file kind hash size
  case "$2" in
    sha256) [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$3" ] ;;
    gitsha1) [ "$( (printf 'blob %s\0' "$4"; cat "$1") | sha1sum | cut -d' ' -f1)" = "$3" ] ;;
  esac
}

fetch() { # url dest size kind hash
  local url=$1 dest=$2 size=$3 kind=$4 hash=$5
  mkdir -p "$(dirname "$dest")"
  if [ -f "$dest" ] && [ "$(stat -c %s "$dest")" = "$size" ] && verify "$dest" "$kind" "$hash" "$size"; then
    echo "have  $dest"; return 0
  fi
  for try in 1 2 3 4 5 6 7 8; do
    have=$(stat -c %s "$dest.part" 2>/dev/null || echo 0)
    [ "$have" -gt "$size" ] && rm -f "$dest.part"
    [ "$have" = "$size" ] && break
    echo "$(date +%T) get   $dest (try $try, have $have of $size)"
    curl -fsSL --retry 5 --retry-delay 10 --connect-timeout 30 -C - -o "$dest.part" "$url" && break
    sleep 15
  done
  if [ "$(stat -c %s "$dest.part" 2>/dev/null || echo 0)" != "$size" ]; then echo "FAIL  size $dest"; return 1; fi
  if ! verify "$dest.part" "$kind" "$hash" "$size"; then echo "FAIL  hash $dest"; mv "$dest.part" "$dest.bad"; return 1; fi
  mv "$dest.part" "$dest"; echo "$(date +%T) ok    $dest"
}

fail=0
for m in "${MANIFESTS[@]}"; do
  while IFS=$'\t' read -r url out size kind hash; do
    [ -z "$url" ] && continue
    fetch "$url" "$BASE/$out" "$size" "$kind" "$hash" || fail=1
  done < "$m"
done
[ $fail = 0 ] && echo "ALL DONE" || echo "SOME FAILED (rerun to resume)"
exit $fail
