# ans-cleanup

Ansible cleanup and reconciliation tasks for the homelab, run from AWX. Each task is its own playbook
(`playbook_*.yml`), so each gets its own AWX job template, and each is safe to re-run.

This repository is public: everything committed here is a placeholder. Real hosts and credentials come from the
AWX inventory and credentials. Never commit them.

## Tasks

| Playbook | What it does |
| --- | --- |
| [playbook_netbox_ip_sync.yml](playbook_netbox_ip_sync.yml) | Records every Proxmox VM's IP address in NetBox: creates missing records, fills in empty fields, reports conflicts. Report only unless `netbox_ip_sync_apply: true`. See [roles/netbox_ip_sync](roles/netbox_ip_sync/README.md). |
| [playbook_grow_root_fs.yml](playbook_grow_root_fs.yml) | Grows each Linux VM's root partition, LVM volume and filesystem into all the space on its virtual disk, for VMs whose disk was made bigger but whose OS never saw the space. Report only unless `grow_root_fs_apply: true`. See [roles/grow_root_fs](roles/grow_root_fs/README.md). |

## AWX setup

1. **Project:** this repository.
2. **Inventory:** [inventory/hosts.yml](inventory/hosts.yml) shows the shape.
   - `proxmox` (netbox_ip_sync): one node of each Proxmox cluster (any node can read the whole cluster),
     connecting as `root` like proxmox-deploy.
   - `linux` (grow_root_fs): the Linux VMs, connecting as the admin user proxmox-deploy's cloud-init creates.
     To use another group, set `grow_root_fs_hosts`.
3. **Credentials:**
   - A Machine credential for the Proxmox node, and one (SSH key) for the Linux VMs.
   - The NetBox credential already used by proxmox-deploy, or a custom credential type that injects
     `netbox_api_url` and `netbox_token` as extra vars.
4. **Job templates**, one per task and mode, for example:
   - "NetBox IP sync (report)": `playbook_netbox_ip_sync.yml`.
   - "NetBox IP sync (apply)": the same playbook with extra var `netbox_ip_sync_apply: true`.
   - "Grow root filesystems (report)" and "(apply)": `playbook_grow_root_fs.yml`, the apply one with extra var
     `grow_root_fs_apply: true`. Use the job template's limit to pick VMs.

   Set non-secret settings such as `netbox_ip_sync_fallback_domain` on the inventory or as extra vars.

Run the report first, review the conflicts and skipped VMs, then run apply.

## Development and CI

The workflows and lint configs match the other `SAL9000-HomeLab` Ansible repositories:

- **Ansible CI** (`.github/workflows/ansible-ci.yml`), on every pull request: `yamllint`,
  `ansible-playbook --syntax-check` on each root `playbook*.yml` / `site*.yml`, and `ansible-lint`.
- **Linting Validation** (`.github/workflows/ci.yml`), on pull requests: markdownlint, linkspector, yamllint.

Run the same checks locally:

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install "yamllint>=1.30" "ansible>=2.15" "ansible-lint>=6"
yamllint -f parsable .
for pb in playbook*.yml; do ansible-playbook -i inventory/hosts.yml --syntax-check "$pb"; done
ansible-lint .
npx markdownlint-cli2 "**/*.md" "#.venv"
```

Add a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) with each change. Pushing a `vX.Y.Z` tag
publishes that version's section as a GitHub release.
