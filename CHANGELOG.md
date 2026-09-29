# Changelog

All notable changes to this project are documented here. Releases are cut by
pushing a `vX.Y.Z` tag; the release workflow publishes the matching section.

## [Unreleased]

- Added: `netbox_ip_sync` role and `playbook_netbox_ip_sync.yml`: records every Proxmox VM's IP address in
  NetBox (static cloud-init address, else the guest agent's). Creates missing records, fills in empty fields,
  reports conflicts; report only unless `netbox_ip_sync_apply: true`. `dns_name` comes from the address's
  reverse DNS name, else `netbox_ip_sync_fallback_domain`.
