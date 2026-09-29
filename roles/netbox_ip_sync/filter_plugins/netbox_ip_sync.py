# roles/netbox_ip_sync/filter_plugins/netbox_ip_sync.py
"""Filters that turn the Proxmox VM list and NetBox lookups into a sync plan.

netbox_ip_sync_candidates: one candidate per IP address found on a VM, plus VMs skipped and addresses
    claimed by more than one VM.
netbox_ip_sync_plan: compares candidates with the NetBox records for their addresses and decides what to
    create, which gaps to fill, and which records conflict.
"""
import re


def _short(name):
    return (name or "").strip().lower().split(".")[0]


def _dns_name(vm_name, ptr, fallback_domain):
    """dns_name for a VM's address, and where it came from.

    1. The reverse DNS (PTR) name, if its first label is the VM's name.
    2. The VM name itself, if it's already a fully qualified name.
    3. <vm name>.<fallback_domain>, if a fallback domain is set.
    A PTR name for a different host is ignored (and reported by the caller).
    """
    if ptr and _short(ptr) == _short(vm_name):
        return ptr.lower(), "reverse DNS"
    if "." in vm_name:
        return vm_name.lower(), "VM name"
    if fallback_domain:
        return f"{vm_name}.{fallback_domain}".lower(), "fallback domain"
    return "", ""


def netbox_ip_sync_candidates(vms, exclude_patterns=None, fallback_domain="", description_format="{name}"):
    exclude = [re.compile(p) for p in (exclude_patterns or [])]
    skipped = []
    notes = []
    claims = {}
    for vm in vms or []:
        name = vm.get("name", "")
        if any(p.search(name) for p in exclude):
            skipped.append({"vm": name, "vmid": vm.get("vmid"), "reason": "excluded by netbox_ip_sync_exclude"})
            continue
        if not vm.get("addresses"):
            reason = "; ".join(vm.get("notes") or []) or "no addresses found"
            skipped.append({"vm": name, "vmid": vm.get("vmid"), "reason": reason})
            continue
        for addr in vm["addresses"]:
            ip = addr["address"].split("/")[0]
            ptr = addr.get("ptr", "")
            dns_name, dns_source = _dns_name(name, ptr, fallback_domain)
            if ptr and _short(ptr) != _short(name):
                notes.append(f"{ip}: reverse DNS says {ptr}, not {name}; not used for dns_name")
            claims.setdefault(ip, []).append({
                "ip": ip,
                "address": addr["address"],
                "source": addr.get("source", ""),
                "vm": name,
                "vmid": vm.get("vmid"),
                "node": vm.get("node", ""),
                "dns_name": dns_name,
                "dns_source": dns_source,
                "description": description_format.format(
                    name=name, vmid=vm.get("vmid"), node=vm.get("node", "")),
            })

    candidates = []
    duplicates = []
    for ip in sorted(claims, key=lambda a: [int(p) if p.isdigit() else p for p in re.split(r"[.:]", a)]):
        owners = claims[ip]
        vmids = {c["vmid"] for c in owners}
        if len(vmids) > 1:
            duplicates.append({
                "ip": ip,
                "reason": "address found on more than one VM: "
                          + ", ".join(sorted(f"{c['vm']} ({c['vmid']})" for c in owners)),
            })
            continue
        candidates.append(owners[0])
    return {"candidates": candidates, "skipped": skipped, "duplicates": duplicates, "notes": notes}


def _owner_of(record):
    """Host a NetBox IP record names, from its interface assignment or dns_name."""
    assigned = record.get("assigned_object") or {}
    for key in ("virtual_machine", "device"):
        parent = assigned.get(key) or {}
        if parent.get("name"):
            return parent["name"], f"assigned to {key.replace('_', ' ')} {parent['name']}"
    if record.get("dns_name"):
        return record["dns_name"], f"dns_name {record['dns_name']}"
    return "", ""


def _dns_label(cand):
    if cand["dns_name"]:
        return f"dns_name {cand['dns_name']} ({cand['dns_source']})"
    return "no dns_name (no reverse DNS and no fallback domain)"


def netbox_ip_sync_plan(candidates, lookups, tags=None, status="active"):
    """lookups: the `results` list of a looped uri task, one result per candidate (item = candidate)."""
    tags = list(tags or [])
    found = {}
    for res in lookups or []:
        item = res.get("item") or {}
        found[item.get("ip")] = ((res.get("json") or {}).get("results")) or []

    plan = {"create": [], "fill": [], "unchanged": [], "conflicts": [], "notes": []}
    for cand in candidates or []:
        label = f"{cand['address']} {cand['vm']} ({cand['vmid']}, {cand['source']})"
        records = found.get(cand["ip"], [])

        if not records:
            body = {"address": cand["address"], "status": status, "description": cand["description"]}
            if cand["dns_name"]:
                body["dns_name"] = cand["dns_name"]
            if tags:
                body["tags"] = [{"name": t} for t in tags]
            plan["create"].append({"label": f"{label}: {_dns_label(cand)}", "body": body})
            continue

        if len(records) > 1:
            plan["conflicts"].append({
                "label": label,
                "reason": f"{len(records)} NetBox records have this address (different VRFs or duplicates)",
            })
            continue

        record = records[0]
        owner, why = _owner_of(record)
        if owner and _short(owner) != _short(cand["vm"]):
            plan["conflicts"].append({
                "label": label,
                "reason": f"NetBox record {record.get('id')} belongs to another host ({why})",
            })
            continue

        patch = {}
        if not record.get("dns_name") and cand["dns_name"]:
            patch["dns_name"] = cand["dns_name"]
            label = f"{label}: {_dns_label(cand)}"
        if not record.get("description"):
            patch["description"] = cand["description"]
        existing_tags = [t.get("name") for t in record.get("tags") or [] if t.get("name")]
        known = {t.lower() for t in existing_tags} | {
            (t.get("slug") or "").lower() for t in record.get("tags") or []}
        missing = [t for t in tags if t.lower() not in known]
        if missing:
            patch["tags"] = [{"name": t} for t in existing_tags + missing]

        if record.get("address") and record["address"] != cand["address"]:
            plan["notes"].append(
                f"{cand['ip']}: NetBox has {record['address']}, the VM uses {cand['address']} (left unchanged)")

        if patch:
            plan["fill"].append({
                "label": label,
                "changes": sorted(patch),
                "body": dict(patch, id=record["id"]),
            })
        else:
            plan["unchanged"].append({"label": label})
    return plan


def netbox_ip_sync_update_label(entry):
    return f"{entry['label']}: set {', '.join(entry['changes'])}"


def netbox_ip_sync_conflict_label(entry):
    return f"{entry.get('label') or entry.get('ip')}: {entry['reason']}"


def netbox_ip_sync_skip_label(entry):
    return f"{entry['vm']} ({entry['vmid']}): {entry['reason']}"


class FilterModule:
    def filters(self):
        return {
            "netbox_ip_sync_candidates": netbox_ip_sync_candidates,
            "netbox_ip_sync_plan": netbox_ip_sync_plan,
            "netbox_ip_sync_update_label": netbox_ip_sync_update_label,
            "netbox_ip_sync_conflict_label": netbox_ip_sync_conflict_label,
            "netbox_ip_sync_skip_label": netbox_ip_sync_skip_label,
        }
