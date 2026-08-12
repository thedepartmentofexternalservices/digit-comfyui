# Ansible integration (dxs fleet)

This repo does **not** contain your dxs Ansible inventory or vault. You develop
`comfyui-digit` here; Ansible on your side configures VMs, secrets, and deploy.

## How the pieces fit

```text
  digit-comfyui (this repo)          dxs Ansible / GCP ops
  -------------------------          ----------------------
  PR + merge code          ------>    git pull on ComfyUI VMs
  scripts/deploy-gcp-...   ------>    (or Ansible wraps the same SSH/git pull)
  FAL_KEY on VM            <------    Ansible vault → /etc/digit/comfyui-env
                                      or systemd EnvironmentFile
  run_h3_integration_on_vm ------>   post-deploy verify on canary host
```

The Cloud Agent **cannot** SSH into your Ansible control node or run dxs playbooks.
It only changes this git repo. Wire the steps below into **your** Ansible repo.

## 1. Ensure API keys reach ComfyUI VMs

Keys must exist where ComfyUI runs (same as today for Seedance/fal). Typical pattern:

```yaml
# group_vars/comfyui_vms.yml (in your dxs Ansible repo — not this repo)
digit_comfyui_env:
  FAL_KEY: "{{ vault_fal_key }}"
  MUAPIAPP_API_KEY: "{{ vault_muapi_api_key }}"
  DIGIT_VM_NAME: "{{ inventory_hostname }}"
```

Template to `/etc/digit/comfyui-env` and reference from the `comfyui` systemd unit:

```ini
EnvironmentFile=/etc/digit/comfyui-env
```

`scripts/run_h3_integration_on_vm.sh` sources `/etc/digit/comfyui-env` if present.

## 2. Post-deploy verify playbook (copy into dxs Ansible)

```yaml
---
# playbooks/comfyui-verify-minimax-h3.yml
- name: Verify MiniMax H3 endpoints on ComfyUI canary
  hosts: comfyui_canary   # one host, e.g. comfyui-04
  become: false
  vars:
    digit_node_dir: /opt/ComfyUI/custom_nodes/comfyui-digit
    h3_verify_duration: 4
    h3_verify_modes: text_to_video,image_to_video   # add reference_to_video if desired
  environment:
    FAL_KEY: "{{ vault_fal_key }}"
    MUAPIAPP_API_KEY: "{{ vault_muapi_api_key }}"
  tasks:
    - name: Run H3 live integration test
      ansible.builtin.command:
        cmd: >
          ./scripts/run_h3_integration_on_vm.sh
          --provider all
          --modes {{ h3_verify_modes }}
      args:
        chdir: "{{ digit_node_dir }}"
      register: h3_verify
      changed_when: false

    - name: Show verify output
      ansible.builtin.debug:
        var: h3_verify.stdout_lines
```

Run after your usual deploy play:

```bash
ansible-playbook -i inventories/production playbooks/comfyui-deploy.yml
ansible-playbook -i inventories/production playbooks/comfyui-verify-minimax-h3.yml
```

## 3. Deploy from this repo without Ansible

If you use gcloud instead of Ansible for git pull:

```bash
./scripts/deploy-gcp-comfyui.sh

# Optional: run H3 verify on the first matching instance only
RUN_H3_VERIFY=1 ./scripts/deploy-gcp-comfyui.sh
```

Requires `FAL_KEY` / `MUAPIAPP_API_KEY` already on the VM (via Ansible or manual).

## 4. Local / CI (no Ansible)

From a laptop with keys exported:

```bash
export FAL_KEY=...
export MUAPIAPP_API_KEY=...
python scripts/manual/h3_integration_test.py
```

Pytest (skipped in default CI):

```bash
pytest -m integration tests/test_h3_integration_live.py -v --override-ini "addopts="
```

## What to ask ops to add in dxs Ansible

1. Vault entries: `vault_fal_key`, `vault_muapi_api_key` (if not already present).
2. `EnvironmentFile` or `group_vars` so ComfyUI and verify script see the same keys.
3. Canary play `comfyui-verify-minimax-h3.yml` after deploy (snippet above).
4. Optional: set `H3_VERIFY_MODES=reference_to_video` only on canary (R2V costs more).

If you link or open the dxs Ansible repo in a future agent run, we can add the playbook
there directly instead of copying from this example.
