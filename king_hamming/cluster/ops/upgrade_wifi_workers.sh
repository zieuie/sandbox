#!/usr/bin/env bash
set -euo pipefail

# Run from uther. With no arguments, upgrade the seven workers other than midnights.
hosts=(101 102 103 104 105 107 108)
if (($#)); then
    hosts=("$@")
fi
ssh_opts=(-o BatchMode=yes -o ConnectTimeout=10)

upgrade_host() {
    local suffix=$1 host broken package kernel old_boot new_boot booted_kernel attempt rebooted
    host="192.168.4.$suffix"
    echo "=== $host: upgrading ==="

    ssh "${ssh_opts[@]}" "$host" 'sudo -n apt-get update'
    broken=$(ssh "${ssh_opts[@]}" "$host" \
        "dpkg -l 'linux-modules-nvidia-580-*' 2>/dev/null | awk '\$1 ~ /^i[UF]/ && \$2 ~ /^linux-modules-nvidia-580-[0-9]/ {print \$2}'")
    for package in $broken; do
        ssh "${ssh_opts[@]}" "$host" \
            "sudo -n env DEBIAN_FRONTEND=noninteractive apt-get -y purge '$package'"
    done
    ssh "${ssh_opts[@]}" "$host" \
        'sudo -n env DEBIAN_FRONTEND=noninteractive apt-get -o APT::Get::Always-Include-Phased-Updates=true --no-remove -y full-upgrade'
    ssh "${ssh_opts[@]}" "$host" \
        'sudo -n env DEBIAN_FRONTEND=noninteractive apt-get -y install linux-modules-nvidia-580-generic-hwe-24.04'
    ssh "${ssh_opts[@]}" "$host" \
        'sudo -n env DEBIAN_FRONTEND=noninteractive apt-get -y purge nvidia-dkms-580'

    kernel=$(ssh "${ssh_opts[@]}" "$host" 'basename "$(ls -1 /boot/vmlinuz-* | sort -V | tail -1)"')
    kernel=${kernel#vmlinuz-}
    ssh "${ssh_opts[@]}" "$host" \
        "modinfo -k '$kernel' -F signer nvidia | grep -Fx 'Canonical Ltd. Kernel Module Signing'"
    ssh "${ssh_opts[@]}" "$host" "modinfo -k '$kernel' -n iwlwifi"

    old_boot=$(ssh "${ssh_opts[@]}" "$host" 'cat /proc/sys/kernel/random/boot_id')
    echo "=== $host: rebooting into $kernel ==="
    ssh "${ssh_opts[@]}" "$host" 'sudo -n systemctl reboot'

    rebooted=false
    for ((attempt=0; attempt<60; attempt++)); do
        sleep 5
        if new_boot=$(ssh "${ssh_opts[@]}" "$host" 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null) && [[ $new_boot != "$old_boot" ]]; then
            rebooted=true
            break
        fi
    done
    if [[ $rebooted != true ]]; then
        echo "$host did not return after reboot" >&2
        exit 1
    fi

    booted_kernel=$(ssh "${ssh_opts[@]}" "$host" 'uname -r')
    if [[ $booted_kernel != "$kernel" ]]; then
        echo "$host booted $booted_kernel, expected $kernel" >&2
        exit 1
    fi
    ssh "${ssh_opts[@]}" "$host" \
        "nmcli -t -f GENERAL.STATE dev show wlp3s0 | grep -q '^GENERAL.STATE:100'"
    ssh "${ssh_opts[@]}" "$host" \
        'mokutil --sb-state && modinfo -F signer nvidia && nvidia-smi --query-gpu=name,driver_version --format=csv,noheader'
    echo "=== $host: done ==="
}

pids=()
for suffix in "${hosts[@]}"; do
    upgrade_host "$suffix" &
    pids+=("$!")
done

status=0
for pid in "${pids[@]}"; do
    wait "$pid" || status=1
done
exit "$status"
