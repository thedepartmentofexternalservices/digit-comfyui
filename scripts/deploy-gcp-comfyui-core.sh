#!/usr/bin/env bash
# Pin ComfyUI core on every running GCP comfy* VM to v0.15.1.
#
# Pipes scripts/sync-comfyui-core.sh over SSH. Does not start stopped VMs.
# The next ansible --tags comfyui play will rewind this unless
# digit-infra-ansible pins the same tag. See ansible/README.md.
#
# Requirements:
#   - gcloud CLI authenticated
#   - SSH/IAP access to target instances
#
# Usage:
#   ./scripts/deploy-gcp-comfyui-core.sh
#   USE_IAP=1 GCP_PROJECT=digit-sandbox ./scripts/deploy-gcp-comfyui-core.sh
#
# Environment variables:
#   GCP_PROJECT        GCP project ID (default: active gcloud config project)
#   INSTANCE_FILTER    gcloud instances list --filter (default: name~'comfy')
#   COMFYUI_DIR        Optional colon-separated core checkout paths on the VM
#   COMFYUI_SERVICE    systemd unit to restart (default: comfyui; empty = skip)
#   COMFYUI_REF        Git tag or SHA (default: v0.15.1)
#   USE_IAP            Set to 1 to tunnel SSH through IAP (default: 0)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC_SCRIPT="${SCRIPT_DIR}/sync-comfyui-core.sh"

GCP_PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
INSTANCE_FILTER="${INSTANCE_FILTER:-name~'comfy'}"
COMFYUI_SERVICE="${COMFYUI_SERVICE-comfyui}"
COMFYUI_REF="${COMFYUI_REF:-v0.15.1}"
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

echo "Pinning ComfyUI ${COMFYUI_REF} on ${#INSTANCES[@]} running instance(s) in project ${GCP_PROJECT}"
echo "Filter: ${INSTANCE_FILTER}"
echo

FAILED=0
for row in "${INSTANCES[@]}"; do
  [[ -n "$row" ]] || continue
  IFS=',' read -r name zone <<< "${row}"
  echo "=== ${name} (${zone}) ==="
  REMOTE_ENV="COMFYUI_REF=${COMFYUI_REF} COMFYUI_SERVICE=${COMFYUI_SERVICE}"
  if [[ -n "${COMFYUI_DIR:-}" ]]; then
    REMOTE_ENV="${REMOTE_ENV} COMFYUI_DIR=${COMFYUI_DIR}"
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

echo "ComfyUI core is on ${COMFYUI_REF}. Artists: hard-refresh the browser tab."
echo "Confirm with GET /digit/health — comfyui_version must be 0.15.1."
echo "Bump digit-infra-ansible comfyui_version to v0.15.1 or the next"
echo "--tags comfyui play will rewind the fleet."
