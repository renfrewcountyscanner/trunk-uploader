#!/usr/bin/env bash
set -euo pipefail

root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
if [[ $# -lt 2 || $# -gt 4 ]]; then
  echo "Usage: $0 [PROFILE] WAV JSON [M4A]" >&2
  exit 2
fi

if [[ -f "$1" && -f "$2" ]]; then
  args=(upload "$@")
else
  if [[ $# -lt 3 || ! -f "$2" || ! -f "$3" ]]; then
    echo "Usage: $0 [PROFILE] WAV JSON [M4A]" >&2
    exit 2
  fi
  args=(upload --profile "$1" "$2" "$3")
  [[ $# -eq 4 ]] && args+=("$4")
fi
exec python3 -m trunk_uploader.cli --config "${TRUNK_UPLOADER_CONFIG:-$root/config/uploader.conf}" "${args[@]}"
