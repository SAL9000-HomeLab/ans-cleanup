# netbox_ip_sync

Makes sure every Proxmox VM's IP address has a record in NetBox. It creates missing records and fills in empty
fields on existing ones, but never overwrites anything already set. Records that belong to another host are
reported as conflicts for you to resolve. By default it only reports; set `netbox_ip_sync_apply: true` to change
NetBox.

Run it with [playbook_netbox_ip_sync.yml](../../playbook_netbox_ip_sync.yml).

## How it works

1. **Reads the cluster through one node.** The play runs on the first host in the `proxmox` group, as root over
   SSH, like proxmox-deploy. A Python script ([files/proxmox_vm_ips.py](files/proxmox_vm_ips.py), standard
   library only) uses `pvesh` to list every QEMU VM in the cluster (templates and containers are skipped) and
   find its addresses:
   - **Static cloud-init addresses** (`ipconfigN: ip=192.0.2.11/24,...`), which is what proxmox-deploy sets.
     These work even when the VM is stopped.
   - **Otherwise, the QEMU guest agent's addresses,** for running VMs with the agent enabled (DHCP or
     hand-built VMs). Loopback, link-local and container/VPN interfaces (`netbox_ip_sync_exclude_interfaces`)
     are ignored.

   With `netbox_ip_sync_reverse_dns` (the default), it also looks up each address's reverse DNS (PTR) name on
   the node, through the node's own resolver, since that node normally uses the lab's DNS servers.
2. **Looks up each address in NetBox** from the controller (the AWX execution environment), by address
   regardless of mask or VRF.
3. **Decides what to do with each address:**

   | NetBox has | Result |
   | --- | --- |
   | No record | **Create** one: address with the VM's mask, `dns_name` (see below), description, tags, status. |
   | One record for this VM, or for no host | **Fill in** `dns_name` and description if they're empty, and add missing tags. Mask, status, VRF and everything else stay as they are. |
   | One record for another host | **Conflict**, left unchanged. "Another host" means its `dns_name` or interface assignment names a different VM or device. |
   | Several records (different VRFs or duplicates) | **Conflict**, left unchanged. |

   An address found on more than one VM is also a conflict. If NetBox's mask differs from the VM's, the record
   is left alone and a note is added.
4. **Reports** the plan, then, with `netbox_ip_sync_apply: true`, creates any missing tags and sends the new
   records and the fill-ins as one bulk request each.

VMs are matched by their name's first label, case-insensitively: `web-01` matches `web-01.lab.example.com`.

## How dns_name is chosen

For new records, and existing ones whose `dns_name` is empty, the first of these that applies:

1. **The address's reverse DNS (PTR) name,** if its first label is the VM's name (e.g. `192.0.2.11` →
   `web-01.lab.example.com` for VM `web-01`).
2. **The VM name itself,** if it's already fully qualified (contains a dot).
3. **`<vm name>.<netbox_ip_sync_fallback_domain>`,** if a fallback domain is set.

If none applies, `dns_name` is left empty. A PTR name for a different host usually means stale DNS or a reused
address. It isn't used, and the report notes it. The report shows each `dns_name` and where it came from.

## Variables

| Variable | Default | Notes |
| --- | --- | --- |
| `netbox_ip_sync_apply` | `false` | `true` changes NetBox; otherwise report only. |
| `netbox_api_url` / `netbox_token` | none | From an AWX credential. The `netbox: {api_url, token, ssl_verify}` mapping used by proxmox-deploy also works. v1 and v2 (`nbt_…`) tokens both work. |
| `netbox_ssl_verify` | `true` | Verify NetBox's certificate. |
| `netbox_ip_sync_reverse_dns` | `true` | Use reverse DNS (PTR) names for `dns_name`. See [How dns_name is chosen](#how-dns_name-is-chosen). |
| `netbox_ip_sync_dns_timeout` | `3` | Seconds per reverse lookup. |
| `netbox_ip_sync_fallback_domain` | `""` | Domain for `dns_name` when there's no matching reverse DNS name, e.g. `lab.example.com`. |
| `netbox_ip_sync_description` | `Proxmox VM {name}` | Placeholders `{name}`, `{vmid}`, `{node}`. |
| `netbox_ip_sync_tags` | `[proxmox]` | Created in NetBox if missing. |
| `netbox_ip_sync_status` | `active` | Status for new records. |
| `netbox_ip_sync_exclude` | `[]` | Python regular expressions matched against VM names, e.g. `['^test-']`. |
| `netbox_ip_sync_include_templates` | `false` | |
| `netbox_ip_sync_guest_agent` | `true` | Fall back to the guest agent. |
| `netbox_ip_sync_guest_agent_timeout` | `10` | Seconds per VM. |
| `netbox_ip_sync_exclude_interfaces` | container, bridge and VPN names | Regular expression of guest interface names to ignore. |
| `netbox_ip_sync_ipv6` | `false` | Also record IPv6 addresses. |

## Reading the report

```text
summary: 14 VMs, 8 addresses: 3 to create, 2 to fill in, 1 already correct, 3 conflicts, 4 VMs skipped
create:      192.0.2.60/24 new-vm (110, ipconfig0): dns_name new-vm.lab.example.com (reverse DNS)
fill_in:     192.0.2.11/24 web-01 (101, ipconfig0): set description, tags
conflicts:   192.0.2.12/24 web-02 (102, ipconfig0): NetBox record 2 belongs to another host (dns_name other-host...)
skipped_vms: stopped-vm (107): no static ipconfig address, and not running (guest agent unavailable)
notes:       192.0.2.13: NetBox has 192.0.2.13/32, the VM uses 192.0.2.13/24 (left unchanged)
```

Each line shows the address, VM name, VMID and where the address came from (`ipconfig0` or `agent:eth0`).
Resolve conflicts in NetBox by hand; the next run picks up the result.
