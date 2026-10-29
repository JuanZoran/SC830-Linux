#!/usr/bin/env bash
set -euo pipefail
project_dir=$(dirname -- "$(readlink -f -- "$0")")
if [ "$EUID" -ne 0 ]; then
  if [ -t 0 ]; then
    exec sudo -- "$project_dir/install-permissions.sh"
  else
    exec pkexec "$project_dir/install-permissions.sh"
  fi
fi
install -m 0644 -- "$project_dir/udev/70-sc830.rules" /etc/udev/rules.d/70-sc830.rules
udevadm control --reload-rules
for device_node in /sys/class/hidraw/hidraw*; do
  [ -f "$device_node/device/uevent" ] || continue
  if rg -q '^HID_ID=0003:0000248A:00005B2[EF]$' "$device_node/device/uevent"; then
    udevadm trigger --action=change "$device_node"
  fi
done
udevadm settle --timeout=5
printf '%s\n' 'SC830 设备权限已安装。'
