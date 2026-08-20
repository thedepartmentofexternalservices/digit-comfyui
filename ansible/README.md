# Ansible integration (dxs fleet)

This repo does **not** contain your dxs Ansible inventory or vault. You develop
`comfyui-digit` here; Ansible on your side configures VMs, secrets, and deploy.

## Fleet pin: always `master`

Do **not** pin `comfyui-digit` to a SHA. `6afeac6` was pre-2.5. `0d9e8e8` (MiniMax H3) is not an ancestor of Seedance 2.5 (`#23`). Either pin rewinds Digit Dance. Track GitHub `master` and force-reset local checkouts. Live master (`c2ddf10`) already has MiniMax (`#15`) and 2.5.

In `roles/comfyui_gcp/defaults/main.yml` (digit-infra-ansible):

```yaml
comfyui_digit_version: master
```

In the custom_nodes git task:

```yaml
- name: Force comfyui-digit to origin/master
  ansible.builtin.git:
    repo: https://github.com/thedepartmentofexternalservices/comfyui-digit.git
    dest: /opt/comfyui/custom_nodes/comfyui-digit
    version: "{{ comfyui_digit_version }}"
    force: true
    update: true
  notify: restart comfyui
```

`force: true` discards cherry-picks and dirty trees. After the play, confirm with `GET /digit/health` — `seedance_models` must include `seedance-2.5`.

## ComfyUI core pin: `v0.15.1`

Travis workflows saved on ComfyUI 0.15.1 flag core `LoadImage` as missing on older fleet boxes. Pin the **core** checkout, not just `comfyui-digit`.

Tag: `v0.15.1`
SHA: `3dd10a59c00248d00f0cb0ab794ff1bb9fb00a5f`
Frontend: `comfyui-frontend-package==1.39.19`

In `roles/comfyui_gcp/defaults/main.yml` (digit-infra-ansible):

```yaml
comfyui_version: v0.15.1
```

The git task that clones `/opt/comfyui` must use that version (tag or SHA). A leftover older SHA will rewind the fleet on the next `--tags comfyui` play.

From a machine with `gcloud` / IAP (this Cloud Agent cannot reach the VMs):

```bash
GCP_PROJECT=digit-sandbox USE_IAP=1 ./scripts/deploy-gcp-comfyui-core.sh
```

Confirm each host with `GET /digit/health` — `comfyui_version` must be `0.15.1`. Canary `comfyui-01` first, then 00/02–06. Stopped `comfy*` boxes get the pin when they boot if ansible is updated.

Flame, studio, and Mac ComfyUI installs are not GCP `comfy*` VMs. Run this on each of those hosts (both Easy-Install and Documents/ComfyUI checkouts on the MBP):

```bash
GIT_REF=master ./scripts/sync-comfyui-digit.sh
```

Then relaunch ComfyUI and hard-refresh the browser.

## How the pieces fit

```text
  digit-comfyui (this repo)          dxs Ansible / GCP ops
  -------------------------          ----------------------
  PR + merge to master   ------>    force-reset every checkout to origin/master
  scripts/sync-comfyui-digit.sh      (local / Flame / Mac)
  scripts/deploy-gcp-comfyui.sh      (running GCP comfy* VMs)
  scripts/deploy-gcp-comfyui-core.sh (pin /opt/comfyui to v0.15.1)
  ansible git version: master        force: true  (do not pin a SHA)
  ansible comfyui_version: v0.15.1   (core pin; SHA 3dd10a59)
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
