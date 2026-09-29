#!/usr/bin/env python3
"""List every QEMU VM in the Proxmox cluster with its IPv4 (optionally IPv6) addresses, as JSON.

Runs on any cluster node: pvesh proxies /nodes/<other>/... requests to the node that owns the VM.
Managed by Ansible (roles/netbox_ip_sync).

Address sources, per VM:
  1. Static addresses in the cloud-init config (ipconfigN: ip=192.0.2.11/24,...). Works for stopped VMs.
  2. Otherwise, if --agent is set and the VM is running with the guest agent enabled, the addresses the
     QEMU guest agent reports, skipping loopback, link-local and interfaces matching --exclude-interfaces.

With --reverse-dns, each address also gets "ptr": the name its reverse DNS (PTR) lookup returns on this node,
through its normal resolver (getent), or "" if there is none.

Output: {"vms": [{"vmid", "name", "node", "status", "template", "addresses": [{"address", "source", "ptr"}],
"notes": [...]}], "errors": [...]}
"""
import argparse
import concurrent.futures
import ipaddress
import json
import re
import subprocess
import sys


def pvesh(path, timeout, *extra):
    """Run `pvesh get PATH` and return the parsed JSON."""
    out = subprocess.run(
        ["pvesh", "get", path, "--output-format", "json", *extra],
        capture_output=True, text=True, timeout=timeout, check=True,
    )
    return json.loads(out.stdout or "null")


def config_addresses(config, include_ipv6):
    """Static addresses from ipconfigN entries (dhcp/auto entries are skipped)."""
    found = []
    for key in sorted(k for k in config if re.fullmatch(r"ipconfig\d+", k)):
        for part in str(config[key]).split(","):
            name, _, value = part.partition("=")
            if name == "ip" or (include_ipv6 and name == "ip6"):
                try:
                    iface = ipaddress.ip_interface(value)
                except ValueError:
                    continue  # dhcp, auto, or malformed
                found.append({"address": str(iface), "source": key})
    return found


def agent_enabled(config):
    value = str(config.get("agent", "0"))
    first = value.split(",")[0]
    return first in ("1", "enabled=1") or "enabled=1" in value.split(",")


def agent_addresses(node, vmid, timeout, exclude_re, include_ipv6):
    data = pvesh(f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces", timeout)
    found = []
    for iface in (data or {}).get("result", []):
        name = iface.get("name", "")
        if exclude_re.fullmatch(name):
            continue
        for addr in iface.get("ip-addresses", []) or []:
            family = addr.get("ip-address-type")
            if family == "ipv6" and not include_ipv6:
                continue
            if family not in ("ipv4", "ipv6"):
                continue
            try:
                ip = ipaddress.ip_interface(f"{addr['ip-address']}/{addr['prefix']}")
            except (KeyError, ValueError):
                continue
            if ip.ip.is_loopback or ip.ip.is_link_local or ip.ip.is_multicast:
                continue
            found.append({"address": str(ip), "source": f"agent:{name}"})
    return found


def reverse_lookup(ip, timeout):
    """PTR name for ip from the system resolver, or "" (no record, timeout, or error)."""
    try:
        out = subprocess.run(["getent", "hosts", ip], capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return ""
    fields = out.stdout.split()
    if out.returncode != 0 or len(fields) < 2:
        return ""
    return fields[1].rstrip(".").lower()


def add_reverse_names(vms, timeout):
    """Look up every address in parallel and store the result as addr["ptr"]."""
    addrs = [a for vm in vms for a in vm["addresses"]]
    ips = sorted({a["address"].split("/")[0] for a in addrs})
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        names = dict(zip(ips, pool.map(lambda ip: reverse_lookup(ip, timeout), ips), strict=True))
    for a in addrs:
        a["ptr"] = names.get(a["address"].split("/")[0], "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", action="store_true", help="fall back to the QEMU guest agent")
    parser.add_argument("--agent-timeout", type=int, default=10)
    parser.add_argument("--exclude-interfaces", default="^$", help="regex of guest interface names to skip")
    parser.add_argument("--include-templates", action="store_true")
    parser.add_argument("--ipv6", action="store_true")
    parser.add_argument("--reverse-dns", action="store_true", help="add each address's PTR name")
    parser.add_argument("--dns-timeout", type=int, default=3)
    args = parser.parse_args()
    exclude_re = re.compile(args.exclude_interfaces)

    result = {"vms": [], "errors": []}
    try:
        resources = pvesh("/cluster/resources", 60, "--type", "vm")
    except (subprocess.SubprocessError, OSError, ValueError) as err:
        print(json.dumps({"vms": [], "errors": [f"cluster/resources: {err}"]}))
        return 1

    for res in sorted(resources or [], key=lambda r: r.get("vmid", 0)):
        if res.get("type") != "qemu":
            continue
        if res.get("template") and not args.include_templates:
            continue
        vm = {
            "vmid": res.get("vmid"),
            "name": res.get("name", ""),
            "node": res.get("node", ""),
            "status": res.get("status", ""),
            "template": bool(res.get("template")),
            "addresses": [],
            "notes": [],
        }
        try:
            config = pvesh(f"/nodes/{vm['node']}/qemu/{vm['vmid']}/config", 30) or {}
        except (subprocess.SubprocessError, OSError, ValueError) as err:
            result["errors"].append(f"{vm['name']} ({vm['vmid']}): config: {err}")
            result["vms"].append(vm)
            continue

        vm["addresses"] = config_addresses(config, args.ipv6)
        if not vm["addresses"]:
            if not args.agent:
                vm["notes"].append("no static ipconfig address")
            elif vm["status"] != "running":
                vm["notes"].append("no static ipconfig address, and not running (guest agent unavailable)")
            elif not agent_enabled(config):
                vm["notes"].append("no static ipconfig address, and guest agent not enabled")
            else:
                try:
                    vm["addresses"] = agent_addresses(
                        vm["node"], vm["vmid"], args.agent_timeout, exclude_re, args.ipv6)
                    if not vm["addresses"]:
                        vm["notes"].append("guest agent reported no usable addresses")
                except subprocess.TimeoutExpired:
                    vm["notes"].append("guest agent did not answer in time")
                except (subprocess.SubprocessError, OSError, ValueError):
                    vm["notes"].append("guest agent not responding")
        result["vms"].append(vm)

    for vm in result["vms"]:
        for addr in vm["addresses"]:
            addr.setdefault("ptr", "")
    if args.reverse_dns:
        add_reverse_names(result["vms"], args.dns_timeout)

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
