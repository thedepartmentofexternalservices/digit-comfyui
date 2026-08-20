#!/usr/bin/env bash
# Pin every ComfyUI core checkout on this machine to COMFYUI_REF (default v0.15.1).
#
# Does not touch custom_nodes, models, or user data. Frontend packages that
# ship with this tag are upgraded when a venv is present.
#
# Usage (on a ComfyUI host):
#   ./scripts/sync-comfyui-core.sh
#   COMFYUI_REF=v0.15.1 ./scripts/sync-comfyui-core.sh /opt/comfyui
#
# Usage (piped over SSH from deploy-gcp-comfyui-core.sh):
#   ssh host 'env COMFYUI_REF=v0.15.1 bash -s' < scripts/sync-comfyui-core.sh
#
# Bash 3 compatible (macOS /usr/bin/bash).

set -euo pipefail

COMFYUI_REF="${COMFYUI_REF:-v0.15.1}"
REMOTE="${COMFYUI_GIT_REMOTE:-origin}"
COMFYUI_SERVICE="${COMFYUI_SERVICE-comfyui}"
INSTALL_FRONTEND="${INSTALL_FRONTEND:-1}"

# Locked to ComfyUI v0.15.1 (3dd10a59c00248d00f0cb0ab794ff1bb9fb00a5f).
FRONTEND_PACKAGES="${FRONTEND_PACKAGES:-comfyui-frontend-package==1.39.19 comfyui-workflow-templates==0.9.4 comfyui-embedded-docs==0.4.3}"

git_in() {
  local dir="$1"
  shift
  git -C "$dir" -c "safe.directory=${dir}" "$@"
}

is_comfyui_checkout() {
  local dir="$1"
  [ -d "${dir}/.git" ] || return 1
  [ -f "${dir}/comfyui_version.py" ] || [ -f "${dir}/main.py" ] || return 1
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

  if [ -n "${COMFYUI_DIR:-}" ]; then
    local old_ifs="$IFS"
    IFS=':'
    # shellcheck disable=SC2086
    set -- ${COMFYUI_DIR}
    IFS="$old_ifs"
    for dir in "$@"; do
      raw="${raw}${dir}"$'\n'
    done
  fi

  if [ -z "$raw" ]; then
    for dir in \
      /opt/comfyui \
      /opt/ComfyUI \
      /home/comfyui/ComfyUI \
      "${HOME}/ComfyUI" \
      "${HOME}/Documents/ComfyUI" \
      "${HOME}/ComfyUI-Easy-Install/ComfyUI"
    do
      raw="${raw}${dir}"$'\n'
    done
  fi

  while IFS= read -r dir; do
    [ -n "$dir" ] || continue
    dir="${dir%/}"
    already_listed "$dir" "$unique" && continue
    is_comfyui_checkout "$dir" || continue
    unique="${unique}${dir}"$'\n'
  done <<EOF
$raw
EOF
  printf '%s' "$unique"
}

read_version() {
  local dir="$1"
  if [ -f "${dir}/comfyui_version.py" ]; then
    python3 -c "import ast, pathlib; p=pathlib.Path(r'''${dir}/comfyui_version.py'''); ns={}; exec(p.read_text(), ns); print(ns.get('__version__',''))" 2>/dev/null || true
  fi
}

install_frontend() {
  local dir="$1"
  [ "${INSTALL_FRONTEND}" = "1" ] || return 0
  local python=""
  if [ -n "${COMFYUI_PYTHON:-}" ] && [ -x "${COMFYUI_PYTHON}" ]; then
    python="${COMFYUI_PYTHON}"
  elif [ -x "${dir}/venv/bin/python" ]; then
    python="${dir}/venv/bin/python"
  elif [ -x "${dir}/.venv/bin/python" ]; then
    python="${dir}/.venv/bin/python"
  else
    echo "WARN: no ComfyUI venv at ${dir}; skipped frontend packages" >&2
    return 0
  fi
  # shellcheck disable=SC2086
  "$python" -m pip install --upgrade ${FRONTEND_PACKAGES}
  echo "Installed frontend packages with ${python}"
}

sync_one() {
  local dir="$1"
  local before after before_ver after_ver
  before="$(git_in "$dir" rev-parse --short HEAD)"
  before_ver="$(read_version "$dir")"
  git_in "$dir" fetch --prune --tags "$REMOTE"
  if ! git_in "$dir" rev-parse --verify "${COMFYUI_REF}^{commit}" >/dev/null 2>&1; then
    echo "ERROR: ${dir} has no ${COMFYUI_REF} after fetch" >&2
    return 1
  fi
  git_in "$dir" checkout -f "${COMFYUI_REF}"
  git_in "$dir" reset --hard "${COMFYUI_REF}"
  after="$(git_in "$dir" rev-parse --short HEAD)"
  after_ver="$(read_version "$dir")"
  install_frontend "$dir"
  echo "OK $(hostname) ${dir} ${before} (${before_ver:-unknown}) -> ${after} (${after_ver:-unknown}) (${COMFYUI_REF})"
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
  echo "ERROR: no ComfyUI core git checkout found on $(hostname)" >&2
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
  echo "One or more ComfyUI core checkouts failed on $(hostname)." >&2
  exit 1
fi

restart_comfyui
