# Changelog

All notable changes to this project are documented here. Releases are cut by
pushing a `vX.Y.Z` tag; the release workflow publishes the matching section.

## [Unreleased]

- Added: `grow_root_fs` role and `playbook_grow_root_fs.yml`: grows each Linux VM's root partition, LVM physical
  and logical volume, and XFS or ext4 filesystem into all the space on its virtual disk (for Rocky VMs whose
  disk proxmox-deploy enlarged but whose LVM root stayed at the template's size). Report only unless
  `grow_root_fs_apply: true`.
- Changed: Synced the shared files from `ans-template`: `.gitignore` also ignores `.ansible/` and Molecule state,
  yamllint and ansible-lint skip `.ansible/`, and `.vscode/cspell.json` is shared, with this repository's words in
  `.vscode/project-words.txt`.
- Added: `netbox_ip_sync` role and `playbook_netbox_ip_sync.yml`: records every Proxmox VM's IP address in
  NetBox (static cloud-init address, else the guest agent's). Creates missing records, fills in empty fields,
  reports conflicts; report only unless `netbox_ip_sync_apply: true`. `dns_name` comes from the address's
  reverse DNS name, else `netbox_ip_sync_fallback_domain`.
