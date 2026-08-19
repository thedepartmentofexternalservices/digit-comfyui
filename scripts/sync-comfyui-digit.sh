#!/usr/bin/env bash
# Force every comfyui-digit checkout on this machine to match origin/<GIT_REF>.
#
# Discards local cherry-picks, detached HEADs, and uncommitted edits in the
# pack. That is the point: every ComfyUI install tracks the GitHub repo.
#
# Usage (on a ComfyUI host):
#   ./scripts/sync-comfyui-digit.sh
#   GIT_REF=master ./scripts/sync-comfyui-digit.sh /opt/comfyui/custom_nodes/comfyui-digit
#   DIGIT_NODE_DIR=/path/one:/path/two ./scripts/sync-comfyui-digit.sh
#
# Usage (piped over SSH from deploy-gcp-comfyui.sh):
#   ssh host 'env GIT_REF=master bash -s' < scripts/sync-comfyui-digit.sh
#
# Bash 3 compatible (macOS /usr/bin/bash).

set -euo pipefail

GIT_REF="${GIT_REF:-master}"
REMOTE="${DIGIT_GIT_REMOTE:-origin}"
COMFYUI_SERVICE="${COMFYUI_SERVICE-comfyui}"

git_in() {
  local dir="$1"
  shift
  git -C "$dir" -c "safe.directory=${dir}" "$@"
}

is_digit_checkout() {
  local dir="$1"
  [ -d "${dir}/.git" ] || return 1
  git_in "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1
}

already_listed() {
  local needle="$1"
  local list="$2"
  local existing
  while IFS= read -r existing; do
    [ -n "$existing" ] || continue
    [ "$existing" = "$needle" ] && return 0
  done <<EOF
$list
EOF
  return 1
}

collect_dirs() {
  local raw=""
  local dir
  local unique=""

  if [ "$#" -gt 0 ]; then
    for dir in "$@"; do
      raw="${raw}${dir}"$'\n'
    done
  fi

  if [ -n "${DIGIT_NODE_DIR:-}" ]; then
    local old_ifs="$IFS"
    IFS=':'
    # shellcheck disable=SC2086
    set -- ${DIGIT_NODE_DIR}
    IFS="$old_ifs"
    for dir in "$@"; do
      raw="${raw}${dir}"$'\n'
    done
  fi

  if [ -z "$raw" ]; then
    for dir in \
      /opt/comfyui/custom_nodes/comfyui-digit \
      /opt/ComfyUI/custom_nodes/comfyui-digit \
      /home/comfyui/ComfyUI/custom_nodes/comfyui-digit \
      "${HOME}/ComfyUI/custom_nodes/comfyui-digit" \
      "${HOME}/Documents/ComfyUI/custom_nodes/comfyui-digit" \
      "${HOME}/ComfyUI-Easy-Install/ComfyUI/custom_nodes/comfyui-digit"
    do
      raw="${raw}${dir}"$'\n'
    done
    local root
    for root in /opt /home /Users; do
      [ -d "$root" ] || continue
      while IFS= read -r dir; do
        [ -n "$dir" ] || continue
        raw="${raw}${dir}"$'\n'
      done <<EOF
$(find "$root" -maxdepth 8 -type d -path '*/custom_nodes/comfyui-digit' 2>/dev/null || true)
EOF
    done
  fi

  while IFS= read -r dir; do
    [ -n "$dir" ] || continue
    dir="${dir%/}"
    already_listed "$dir" "$unique" && continue
    is_digit_checkout "$dir" || continue
    unique="${unique}${dir}"$'\n'
  done <<EOF
$raw
EOF
  printf '%s' "$unique"
}

sync_one() {
  local dir="$1"
  local before after
  before="$(git_in "$dir" rev-parse --short HEAD)"
  git_in "$dir" fetch --prune "$REMOTE"
  if ! git_in "$dir" rev-parse --verify "${REMOTE}/${GIT_REF}" >/dev/null 2>&1; then
    echo "ERROR: ${dir} has no ${REMOTE}/${GIT_REF}" >&2
    return 1
  fi
  git_in "$dir" checkout -f -B "$GIT_REF" "${REMOTE}/${GIT_REF}"
  git_in "$dir" reset --hard "${REMOTE}/${GIT_REF}"
  after="$(git_in "$dir" rev-parse --short HEAD)"
  echo "OK $(hostname) ${dir} ${before} -> ${after} (${GIT_REF})"
}

restart_comfyui() {
  [ -n "${COMFYUI_SERVICE}" ] || return 0
  if command -v systemctl >/dev/null 2>&1 \
      && systemctl list-unit-files "${COMFYUI_SERVICE}.service" >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1; then
      sudo systemctl restart "${COMFYUI_SERVICE}"
    else
      systemctl restart "${COMFYUI_SERVICE}"
    fi
    echo "Restarted ${COMFYUI_SERVICE} on $(hostname)"
  else
    echo "WARN: ${COMFYUI_SERVICE}.service not found on $(hostname); relaunch ComfyUI by hand" >&2
  fi
}

DIRS="$(collect_dirs "$@")"
if [ -z "$DIRS" ]; then
  echo "ERROR: no comfyui-digit git checkout found on $(hostname)" >&2
  exit 1
fi

FAILED=0
while IFS= read -r dir; do
  [ -n "$dir" ] || continue
  if ! sync_one "$dir"; then
    FAILED=1
  fi
done <<EOF
$DIRS
EOF

if [ "${FAILED}" -ne 0 ]; then
  echo "One or more checkouts failed on $(hostname)." >&2
  exit 1
fi

restart_comfyui
