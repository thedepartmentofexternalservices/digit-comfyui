#!/usr/bin/env bash
# Run MiniMax H3 live integration tests on the current machine (a ComfyUI VM).
#
# Intended to be invoked:
#   - Manually on a VM after deploy
#   - From deploy-gcp-comfyui.sh (RUN_H3_VERIFY=1)
#   - From your dxs Ansible playbook (see ansible/README.md)
#
# Requires FAL_KEY and/or MUAPIAPP_API_KEY in the environment. On fleet VMs these
# are usually injected by Ansible/systemd (same vars ComfyUI uses at runtime).
#
# Usage:
#   ./scripts/run_h3_integration_on_vm.sh
#   ./scripts/run_h3_integration_on_vm.sh --provider fal --modes text_to_video

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

DURATION="${H3_VERIFY_DURATION:-4}"
PROVIDER="${H3_VERIFY_PROVIDER:-all}"
MODES="${H3_VERIFY_MODES:-text_to_video,image_to_video}"
OUTPUT_DIR="${H3_VERIFY_OUTPUT_DIR:-}"

if [[ -f "${REPO_ROOT}/.venv/bin/python" ]]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  PYTHON=python
fi

# Load optional env file (Ansible can template this to /etc/digit/comfyui-env)
if [[ -f /etc/digit/comfyui-env ]]; then
  # shellcheck disable=SC1091
  set -a
  source /etc/digit/comfyui-env
  set +a
fi

echo "=== MiniMax H3 integration verify on $(hostname) ==="
echo "Repo: ${REPO_ROOT}"
echo "Python: ${PYTHON}"
echo

ARGS=(scripts/manual/h3_integration_test.py --duration "${DURATION}" --provider "${PROVIDER}" --modes "${MODES}")
if [[ -n "${OUTPUT_DIR}" ]]; then
  ARGS+=(--output-dir "${OUTPUT_DIR}")
fi

exec "${PYTHON}" "${ARGS[@]}" "$@"
