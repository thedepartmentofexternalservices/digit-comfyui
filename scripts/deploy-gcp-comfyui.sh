#!/usr/bin/env bash
# Deploy the latest comfyui-digit from GitHub to every running ComfyUI VM.
#
# Pipes scripts/sync-comfyui-digit.sh over SSH so the VM does not need an
# already-updated copy of the script. Each checkout is reset to origin/master
# (local cherry-picks and stale SHA pins are discarded).
#
# Requirements:
#   - gcloud CLI authenticated (gcloud auth login)
#   - Compute Engine API enabled
#   - SSH/IAP access to target instances
#
# Usage:
#   ./scripts/deploy-gcp-comfyui.sh
#   GCP_PROJECT=my-project INSTANCE_FILTER="labels.app=comfyui" ./scripts/deploy-gcp-comfyui.sh
#
# Environment variables:
#   GCP_PROJECT        GCP project ID (default: active gcloud config project)
#   INSTANCE_FILTER    gcloud instances list --filter value (default: name~'comfy')
#   DIGIT_NODE_DIR     Optional colon-separated checkout paths on the VM.
#                      Unset = discover /opt/comfyui and other known locations.
#   COMFYUI_SERVICE    systemd unit to restart (default: comfyui; set empty to skip)
#   GIT_REF            Git ref to reset to (default: master)
#   USE_IAP            Set to 1 to tunnel SSH through IAP (default: 0)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC_SCRIPT="${SCRIPT_DIR}/sync-comfyui-digit.sh"

GCP_PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
INSTANCE_FILTER="${INSTANCE_FILTER:-name~'comfy'}"
COMFYUI_SERVICE="${COMFYUI_SERVICE-comfyui}"
GIT_REF="${GIT_REF:-master}"
USE_IAP="${USE_IAP:-0}"

if [[ ! -f "${SYNC_SCRIPT}" ]]; then
  echo "ERROR: missing ${SYNC_SCRIPT}" >&2
  exit 1
fi

if [[ -z "${GCP_PROJECT}" || "${GCP_PROJECT}" == "(unset)" ]]; then
  echo "ERROR: Set GCP_PROJECT or run: gcloud config set project YOUR_PROJECT_ID" >&2
  exit 1
fi

SSH_FLAGS=(--quiet)
if [[ "${USE_IAP}" == "1" ]]; then
  SSH_FLAGS+=(--tunnel-through-iap)
fi

list_instances() {
  local status="$1"
  gcloud compute instances list \
    --project="${GCP_PROJECT}" \
    --filter="${INSTANCE_FILTER} AND status=${status}" \
    --format='csv[no-heading](name,zone)'
}

mapfile -t INSTANCES < <(list_instances RUNNING)
mapfile -t STOPPED < <(list_instances TERMINATED)

if [[ "${#STOPPED[@]}" -gt 0 ]]; then
  echo "Stopped instances (not updated this run; they need this sync when they start):"
  for row in "${STOPPED[@]}"; do
    [[ -n "$row" ]] || continue
    IFS=',' read -r name zone <<< "${row}"
    echo "  - ${name} (${zone})"
  done
  echo
fi

if [[ "${#INSTANCES[@]}" -eq 0 || -z "${INSTANCES[0]:-}" ]]; then
  echo "No running instances matched filter: ${INSTANCE_FILTER}" >&2
  exit 1
fi

echo "Resetting ${GIT_REF} on ${#INSTANCES[@]} running instance(s) in project ${GCP_PROJECT}"
echo "Filter: ${INSTANCE_FILTER}"
echo

FAILED=0
for row in "${INSTANCES[@]}"; do
  [[ -n "$row" ]] || continue
  IFS=',' read -r name zone <<< "${row}"
  echo "=== ${name} (${zone}) ==="
  REMOTE_ENV="GIT_REF=${GIT_REF} COMFYUI_SERVICE=${COMFYUI_SERVICE}"
  if [[ -n "${DIGIT_NODE_DIR:-}" ]]; then
    REMOTE_ENV="${REMOTE_ENV} DIGIT_NODE_DIR=${DIGIT_NODE_DIR}"
  fi
  if gcloud compute ssh "${name}" \
      --project="${GCP_PROJECT}" \
      --zone="${zone}" \
      "${SSH_FLAGS[@]}" \
      --command "env ${REMOTE_ENV} bash -s" \
      < "${SYNC_SCRIPT}"; then
    echo "OK: ${name}"
  else
    echo "FAILED: ${name}" >&2
    FAILED=1
  fi
  echo
done

if [[ "${FAILED}" -ne 0 ]]; then
  echo "One or more instances failed." >&2
  exit 1
fi

echo "Deployment complete. Artists: hard-refresh the ComfyUI browser tab."
echo "Flame / Mac / studio hosts are not in this gcloud filter; run"
echo "  GIT_REF=${GIT_REF} ./scripts/sync-comfyui-digit.sh"
echo "on each of those machines (or ansible with version: master, force: true)."
