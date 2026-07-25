#!/usr/bin/env bash
set -euo pipefail

root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
if [[ $# -lt 2 || $# -gt 4 ]]; then
  echo "Usage: $0 [PROFILE] WAV JSON [M4A]" >&2
  exit 2
fi

is_json_path() {
  [[ "${1##*/}" == *.json || "${1##*/}" == *.JSON ]]
}

config="${TRUNK_UPLOADER_CONFIG:-$root/config/uploader.conf}"
lock_suffix="$(printf '%s' "$config" | sha256sum | cut -c1-16)"
lock_file="${TRUNK_UPLOADER_LOCK:-/tmp/trunk-uploader-${lock_suffix}.lock}"

if [[ -f "$1" && -f "$2" ]] && is_json_path "$2"; then
  args=(upload "$@")
else
  if (( $# < 3 )) || [[ ! -f "$2" || ! -f "$3" ]] || ! is_json_path "$3"; then
    echo "Usage: $0 [PROFILE] WAV JSON [M4A]" >&2
    exit 2
  fi
  args=(upload --profile "$1" "$2" "$3")
  [[ $# -eq 4 ]] && args+=("$4")
fi
exec flock -x "$lock_file" python3 -m trunk_uploader.cli --config "$config" "${args[@]}"
