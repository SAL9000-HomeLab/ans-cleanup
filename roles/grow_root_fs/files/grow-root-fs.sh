#!/bin/bash
# grow-root-fs: grow the root filesystem into the space its virtual disk has, for VMs whose disk was
# made bigger. Grows the root partition, then (when / is on LVM) the physical volume and the root
# logical volume, then the filesystem. Steps with nothing to grow are skipped, so it is safe to re-run.
#
# Usage: grow-root-fs [--check]
#   --check  only report what would grow.
# Prints "GROW: ..." for each step that grows (or would grow) something, "SKIP: ..." for a layout it
# doesn't handle, and a final "ROOT: ..." line with the size of /.
#
# The same file is in ans-cleanup (roles/grow_root_fs/files) and proxmox-deploy
# (roles/proxmox_clone/files); change both.
set -euo pipefail
export LC_ALL=C

check=false
case ${1:-} in
  --check) check=true ;;
  '') ;;
  *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac

# The filesystem to grow. Only tests change it.
mnt=${GROW_ROOT_FS_MOUNT:-/}

# Gaps smaller than this are partition alignment or LVM metadata, not space to grow into.
min_gain=$((16 * 1024 * 1024))

# In check mode nothing is grown, so the sizes the later steps look at don't change. Once a step
# would grow, report the steps after it as growing too.
would_grow=false

grow() {  # grow <description> <command...>
  echo "GROW: $1"
  shift
  if $check; then
    would_grow=true
  else
    "$@" >&2
  fi
}

report_root() {
  echo "ROOT: $(df -h --output=source,fstype,size,used,avail,pcent "$mnt" | tail -n 1 | tr -s ' ')"
}

install_growpart() {
  command -v growpart >/dev/null && return
  if command -v dnf >/dev/null; then
    dnf -y -q install cloud-utils-growpart
  elif command -v apt-get >/dev/null; then
    apt-get -y -q install cloud-guest-utils
  fi
  command -v growpart >/dev/null || { echo "growpart is missing and could not be installed" >&2; exit 1; }
}

run_growpart() {  # run_growpart <disk> <partition number>
  install_growpart
  growpart "$1" "$2"
}

# Grow a partition to the end of its disk, if it is the disk's last partition and the disk has
# room after it. Sizes in /sys/class/block are in 512-byte sectors.
grow_partition() {
  local dev name disk start end other free
  dev=$(readlink -f "$1")
  name=${dev##*/}
  [[ -r /sys/class/block/$name/partition ]] || return 0  # a whole disk: no partition to grow
  disk=$(basename "$(readlink -f "/sys/class/block/$name/..")")
  start=$(< "/sys/class/block/$name/start")
  end=$((start + $(< "/sys/class/block/$name/size")))
  for other in /sys/class/block/"$disk"/"$disk"*; do
    if [[ -r $other/start ]] && (($(< "$other/start") > start)); then
      echo "SKIP: $dev is not the last partition on /dev/$disk"
      return 0
    fi
  done
  # 33 sectors at the end of the disk hold GPT's backup table.
  free=$((($(< "/sys/class/block/$disk/size") - end - 33) * 512))
  if ((free >= min_gain)); then
    grow "partition $dev (+$((free / 1024 / 1024)) MiB)" \
      run_growpart "/dev/$disk" "$(< "/sys/class/block/$name/partition")"
  fi
}

grow_pv() {
  local pv=$1 dev_size pv_size extent
  read -r dev_size pv_size extent < <(pvs --noheadings --nosuffix --units b \
    -o dev_size,pv_size,vg_extent_size "$pv")
  # A PV is always up to one extent smaller than its device.
  if $would_grow || ((dev_size - pv_size >= min_gain + extent)); then
    grow "LVM physical volume $pv" pvresize "$pv"
  fi
}

grow_lv() {
  local lv=$1 vg=$2 free_count extent
  read -r free_count extent < <(vgs --noheadings --nosuffix --units b \
    -o vg_free_count,vg_extent_size "$vg")
  if $would_grow || ((free_count * extent >= min_gain)); then
    grow "LVM logical volume $lv (all free space in $vg)" lvextend -l +100%FREE "$lv"
  fi
}

grow_fs() {
  local dev=$1 fstype=$2 fs_bytes command
  case $fstype in
    xfs)
      fs_bytes=$(xfs_info "$mnt" | awk '
        /^data / { for (i = 1; i <= NF; i++) { split($i, kv, "="); gsub(",", "", kv[2]); v[kv[1]] = kv[2] } }
        END { if (v["bsize"] && v["blocks"]) print v["bsize"] * v["blocks"] }')
      command=(xfs_growfs "$mnt")
      ;;
    ext2 | ext3 | ext4)
      fs_bytes=$(dumpe2fs -h "$dev" 2>/dev/null | awk -F: '
        /^Block count:/ { count = $2 } /^Block size:/ { size = $2 } END { if (count && size) print count * size }')
      command=(resize2fs "$dev")
      ;;
    *)
      echo "SKIP: growing $fstype filesystems is not supported"
      return 0
      ;;
  esac
  if [[ -z $fs_bytes ]]; then
    echo "SKIP: could not read the size of the $fstype filesystem on $mnt"
  elif $would_grow || (($(blockdev --getsize64 "$dev") - fs_bytes >= min_gain)); then
    grow "$fstype filesystem on $mnt" "${command[@]}"
  fi
}

# Make SCSI disks re-read their size, in case the disk was grown while the VM was running
# (virtio-blk disks pick up the new size by themselves). Changes nothing on disk.
for rescan in /sys/class/block/*/device/rescan; do
  [[ -w $rescan ]] && echo 1 > "$rescan" || true
done

src=$(findmnt -nvo SOURCE "$mnt")
fstype=$(findmnt -no FSTYPE "$mnt")
if [[ ! -b $src ]]; then
  echo "SKIP: $mnt is on $src, not a block device"
  report_root
  exit 0
fi
type=$(lsblk -ndo TYPE "$src")

case $type in
  lvm)
    # Match the LV by device number: findmnt names it /dev/mapper/<vg>-<lv>, which lvs doesn't take.
    majmin=$(lsblk -ndo MAJ:MIN "$src" | tr -d ' ')
    read -r vg lv < <(lvs --noheadings --separator ' ' -o vg_name,lv_name,lv_kernel_major,lv_kernel_minor |
      awk -v want="$majmin" '$3 ":" $4 == want { print $1, $2 }')
    if [[ -z ${vg:-} ]]; then
      echo "SKIP: no logical volume found for $src"
      report_root
      exit 0
    fi
    for pv in $(pvs --noheadings -o pv_name -S "vg_name=$vg"); do
      grow_partition "$pv"
      grow_pv "$pv"
    done
    grow_lv "$vg/$lv" "$vg"
    ;;
  part | disk)
    grow_partition "$src"
    ;;
  *)
    echo "SKIP: $mnt is on a $type device ($src), which this script does not grow"
    report_root
    exit 0
    ;;
esac

grow_fs "$src" "$fstype"
report_root
