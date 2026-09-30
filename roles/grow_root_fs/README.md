# grow_root_fs

Grows a Linux VM's root filesystem into all the space on its virtual disk. Use it after a VM's disk was made
bigger in Proxmox (by proxmox-deploy's `disk_size`, or by hand) but the space never showed up inside the guest.
By default it only reports; set `grow_root_fs_apply: true` to grow.

Run it with [playbook_grow_root_fs.yml](../../playbook_grow_root_fs.yml).

## Why the space doesn't show up by itself

Proxmox grows the virtual disk, not what's on it. On first boot, cloud-init's `growpart` module grows the root
partition and filesystem, but only when `/` is directly on a partition and the `growpart` command is installed.
The Rocky Linux templates from vm-templates break both conditions: the kickstart uses `autopart --type=lvm`,
so `/` is the `rl/root` logical volume on a physical volume in the disk's last partition, and the minimal install doesn't
include `cloud-utils-growpart`. The disk grows but the partition, the physical volume, the logical volume and
XFS all stay at the template's size. Ubuntu templates (a plain ext4 partition, with `growpart` installed) aren't
affected.

## What it does

[files/grow-root-fs.sh](files/grow-root-fs.sh) runs as root on each host, with bash (no Python needed):

1. Makes SCSI disks re-read their size, in case the disk was grown while the VM was running.
2. Finds the device `/` is mounted from.
3. Grows the partition under it (or, on LVM, under each of the volume group's physical volumes) to the end of
   its disk with `growpart`. It installs `cloud-utils-growpart` (dnf) or `cloud-guest-utils` (apt) if `growpart`
   is missing. It only grows the disk's last partition.
4. On LVM: `pvresize`, then `lvextend -l +100%FREE` on the root logical volume. **All free space in the volume
   group goes to the root logical volume**, including any left free on purpose.
5. Grows the filesystem: `xfs_growfs` for XFS, `resize2fs` for ext2/3/4. Other filesystems (btrfs), and `/` on
   LUKS or other device-mapper targets, are reported as `SKIP` and left alone.

Each step first compares sizes and does nothing when there's less than 16 MiB to gain, so re-runs change nothing.
Everything is done online; no reboot is needed. Growing never shrinks anything, but take a snapshot first if
the VM matters.

The same script is in proxmox-deploy (`roles/proxmox_clone/files/grow-root-fs.sh`), which runs it on each new
Linux VM's first boot. Keep the two copies the same.

## Output

For each host, the lines that start with:

- `GROW:` a step that grew (report mode: would grow). In report mode, the steps after the first `GROW` are
  predicted, since nothing has actually grown yet.
- `SKIP:` a layout it won't touch, and why.
- `ROOT:` the size of `/` afterwards, as `df -h` shows it.

A last task lists the hosts that grew (or would grow). The apply run marks those hosts "changed".

## Variables

| Variable | Default | Description |
| --- | --- | --- |
| `grow_root_fs_apply` | `false` | `false` reports only; `true` grows. |
| `grow_root_fs_hosts` | `linux` | Playbook only: the group or host pattern to run on. An AWX job template's limit narrows it further. |

## Requirements

- SSH access to each VM as a user with passwordless sudo (proxmox-deploy's `linux_admin_user`, `ansible` by
  default).
- `bash`, `util-linux`, and the tools for the layout (`lvm2`, `xfsprogs`, `e2fsprogs`); all are in the Rocky and
  Ubuntu templates. The first grow needs a package repository for `growpart` unless it's installed already.
