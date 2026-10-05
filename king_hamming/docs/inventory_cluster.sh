#!/usr/bin/env bash

set -uo pipefail

readonly DEFAULT_OUTPUT="CLUSTER_INVENTORY.md"
readonly SSH_TIMEOUT_SECONDS=8
TEMPORARY_DIR=""
readonly -a DEFAULT_HOSTS=(
    192.168.4.101
    192.168.4.102
    192.168.4.103
    192.168.4.104
    192.168.4.105
    192.168.4.106
    192.168.4.107
    192.168.4.108
    192.168.4.151
    192.168.4.156
)

cleanup() {
    if [ -n "$TEMPORARY_DIR" ] && [ -d "$TEMPORARY_DIR" ]; then
        rm -rf -- "$TEMPORARY_DIR"
    fi
}

usage() {
    cat <<'EOF'
Collect hardware and operating-system facts from the king_hamming cluster.

Usage:
  ./inventory_cluster.sh --run [-o FILE] [--user USER] [HOST ...]

Example:
  ./inventory_cluster.sh --run -o CLUSTER_INVENTORY.md
  ./inventory_cluster.sh --run --user zooey 192.168.4.101 192.168.4.151

With no HOST arguments, queries 192.168.4.101 through .108, .151 and .156.
With no arguments, prints this help. Existing output files are not overwritten.
SSH uses batch mode and an 8-second connection timeout.
EOF
}

escape_markdown() {
    local value=$1
    value=${value//|/\\|}
    value=${value//$'\n'/<br>}
    printf '%s' "$value"
}

single_line() {
    local key=$1
    local file=$2
    sed -n "s/^${key}=//p" "$file" | head -n 1
}

write_detail() {
    local title=$1
    local key=$2
    local file=$3
    local content
    content=$(sed -n "/^BEGIN_${key}$/,/^END_${key}$/p" "$file" | sed '1d;$d')
    printf '#### %s\n\n```text\n%s\n```\n\n' "$title" "$content"
}

collect_host() {
    local destination=$1
    local output=$2

    ssh \
        -o BatchMode=yes \
        -o ConnectTimeout="${SSH_TIMEOUT_SECONDS}" \
        -o ServerAliveInterval=5 \
        -o ServerAliveCountMax=1 \
        "$destination" 'sh -s' >"$output" 2>&1 <<'REMOTE'
set -u

command_value() {
    if command -v "$1" >/dev/null 2>&1; then
        command "$@" 2>/dev/null || true
    fi
}

first_line() {
    command_value "$@" | head -n 1
}

cpu_model=$(command_value lscpu | sed -n 's/^Model name:[[:space:]]*//p' | head -n 1)
cpu_count=$(command_value getconf _NPROCESSORS_ONLN)
memory_bytes=$(command_value awk '/MemTotal:/ {print $2 * 1024}' /proc/meminfo)
numa_nodes=$(command_value lscpu | sed -n 's/^NUMA node(s):[[:space:]]*//p' | head -n 1)
virtualization=$(command_value systemd-detect-virt)
watchdogs=$(find /dev -maxdepth 1 -name 'watchdog*' -printf '%f ' 2>/dev/null || true)
# NVIDIA GPUs as "name (memory)", joined by "; ". Lines without a memory figure (nvidia-smi's own
# error text when the driver is broken) are dropped.
gpus=$(command_value nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits |
    awk -F', *' 'NF >= 2 && $2 ~ /^[0-9]+$/ {printf "%s%s (%.1f GiB)", sep, $1, $2 / 1024; sep = "; "}')
nvidia_driver=$(command_value nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n 1)

printf 'HOSTNAME=%s\n' "$(first_line hostname -f)"
printf 'KERNEL=%s\n' "$(first_line uname -sr)"
printf 'ARCH=%s\n' "$(first_line uname -m)"
printf 'CPU_MODEL=%s\n' "$cpu_model"
printf 'CPU_COUNT=%s\n' "$cpu_count"
printf 'MEMORY_BYTES=%.0f\n' "${memory_bytes:-0}"
printf 'NUMA_NODES=%s\n' "${numa_nodes:-unknown}"
printf 'VIRTUALIZATION=%s\n' "${virtualization:-none-or-unknown}"
printf 'WATCHDOGS=%s\n' "${watchdogs:-none}"
printf 'GPUS=%s\n' "${gpus:-none}"
printf 'NVIDIA_DRIVER=%s\n' "${nvidia_driver:-none}"
printf 'SYSTEMD=%s\n' "$(command -v systemctl >/dev/null 2>&1 && printf yes || printf no)"
printf 'COMPILER=%s\n' "$(first_line cc --version)"
printf 'UPTIME=%s\n' "$(first_line uptime -p)"

printf 'BEGIN_OS_RELEASE\n'
command_value cat /etc/os-release
printf 'END_OS_RELEASE\n'

printf 'BEGIN_LSCPU\n'
command_value lscpu
printf 'END_LSCPU\n'

printf 'BEGIN_MEMORY\n'
command_value free -h
printf 'END_MEMORY\n'

printf 'BEGIN_NUMA\n'
if command -v numactl >/dev/null 2>&1; then
    command_value numactl --hardware
else
    printf 'numactl is not installed\n'
fi
printf 'END_NUMA\n'

printf 'BEGIN_STORAGE\n'
command_value lsblk -o NAME,TYPE,SIZE,ROTA,FSTYPE,MOUNTPOINTS,MODEL
printf 'END_STORAGE\n'

printf 'BEGIN_FILESYSTEMS\n'
command_value df -hT -x tmpfs -x devtmpfs
printf 'END_FILESYSTEMS\n'

printf 'BEGIN_WATCHDOG\n'
if [ -d /sys/class/watchdog ]; then
    for device in /sys/class/watchdog/watchdog*; do
        [ -e "$device" ] || continue
        printf '%s: ' "$(basename "$device")"
        if [ -r "$device/identity" ]; then
            cat "$device/identity"
        else
            printf 'identity unavailable\n'
        fi
    done
else
    printf 'No /sys/class/watchdog directory\n'
fi
printf 'END_WATCHDOG\n'

printf 'BEGIN_GPU\n'
if command -v nvidia-smi >/dev/null 2>&1; then
    command_value nvidia-smi --query-gpu=index,name,memory.total,driver_version,compute_cap,pci.bus_id --format=csv
else
    printf 'nvidia-smi is not installed\n'
fi
printf 'Display controllers (lspci):\n'
command_value lspci | grep -i -E 'vga|3d|display' || printf 'lspci is not available\n'
printf 'END_GPU\n'

printf 'BEGIN_NETWORK\n'
command_value ip -brief address
command_value ip route show default
printf 'END_NETWORK\n'

printf 'BEGIN_LIMITS\n'
printf 'page_size=%s\n' "$(command_value getconf PAGESIZE)"
printf 'open_files_soft=%s\n' "$(ulimit -Sn)"
printf 'open_files_hard=%s\n' "$(ulimit -Hn)"
if [ -r /sys/kernel/mm/transparent_hugepage/enabled ]; then
    printf 'transparent_hugepages='
    cat /sys/kernel/mm/transparent_hugepage/enabled
fi
printf 'END_LIMITS\n'
REMOTE
}

main() {
    if [ "$#" -eq 0 ] || [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
        usage
        return 0
    fi

    if [ "$1" != "--run" ]; then
        usage >&2
        return 1
    fi
    shift

    local output=$DEFAULT_OUTPUT
    local user=""
    local -a hosts=()
    while [ "$#" -gt 0 ]; do
        case "$1" in
            -o|--output)
                if [ "$#" -lt 2 ]; then
                    printf 'error: %s requires a value\n' "$1" >&2
                    return 1
                fi
                output=$2
                shift 2
                ;;
            --user)
                if [ "$#" -lt 2 ]; then
                    printf 'error: --user requires a value\n' >&2
                    return 1
                fi
                user=$2
                shift 2
                ;;
            --*)
                printf 'error: unknown option: %s\n' "$1" >&2
                return 1
                ;;
            *)
                hosts+=("$1")
                shift
                ;;
        esac
    done

    if [ "${#hosts[@]}" -eq 0 ]; then
        hosts=("${DEFAULT_HOSTS[@]}")
    fi
    if [ -e "$output" ]; then
        printf 'error: output already exists: %s\n' "$output" >&2
        return 1
    fi

    TEMPORARY_DIR=$(mktemp -d)
    trap cleanup EXIT

    local host
    local destination
    local file
    local -a pids=()
    for host in "${hosts[@]}"; do
        destination=$host
        if [ -n "$user" ]; then
            destination="${user}@${host}"
        fi
        file="${TEMPORARY_DIR}/${host}.txt"
        collect_host "$destination" "$file" &
        pids+=("$!")
    done

    local index
    local status
    local -a statuses=()
    for index in "${!pids[@]}"; do
        if wait "${pids[$index]}"; then
            statuses+=(0)
        else
            statuses+=(1)
        fi
    done

    {
        printf '# king_hamming cluster inventory\n\n'
        printf 'Generated: `%s`\n\n' "$(date --iso-8601=seconds)"
        printf 'Command: `inventory_cluster.sh --run`'
        if [ -n "$user" ]; then
            printf ' with SSH user `%s`' "$(escape_markdown "$user")"
        fi
        printf '\n\n'
        printf 'This report contains hardware and operating-system facts needed to size\n'
        printf 'resident fields, DP tiles, matching state, checkpoints, and watchdog setup.\n\n'
        printf '## Summary\n\n'
        printf '| Address | Status | Hostname | Architecture | CPUs | Memory | GPU | NUMA | Watchdog | OS/kernel |\n'
        printf '| --- | --- | --- | --- | ---: | ---: | --- | ---: | --- | --- |\n'

        for index in "${!hosts[@]}"; do
            host=${hosts[$index]}
            file="${TEMPORARY_DIR}/${host}.txt"
            if [ "${statuses[$index]}" -ne 0 ]; then
                printf '| %s | unreachable/error |  |  |  |  |  |  |  |  |\n' "$host"
                continue
            fi
            local hostname arch cpus memory gpu numa watchdog kernel
            hostname=$(escape_markdown "$(single_line HOSTNAME "$file")")
            arch=$(escape_markdown "$(single_line ARCH "$file")")
            cpus=$(escape_markdown "$(single_line CPU_COUNT "$file")")
            memory=$(single_line MEMORY_BYTES "$file")
            if [ "${memory:-0}" -gt 0 ] 2>/dev/null; then
                memory=$(awk -v bytes="$memory" 'BEGIN {printf "%.1f GiB", bytes/1073741824}')
            else
                memory="unknown"
            fi
            gpu=$(escape_markdown "$(single_line GPUS "$file")")
            numa=$(escape_markdown "$(single_line NUMA_NODES "$file")")
            watchdog=$(escape_markdown "$(single_line WATCHDOGS "$file")")
            kernel=$(escape_markdown "$(single_line KERNEL "$file")")
            printf '| %s | ok | %s | %s | %s | %s | %s | %s | %s | %s |\n' \
                "$host" "$hostname" "$arch" "$cpus" "$memory" "$gpu" "$numa" "$watchdog" "$kernel"
        done

        printf '\n## Machine details\n\n'
        for index in "${!hosts[@]}"; do
            host=${hosts[$index]}
            file="${TEMPORARY_DIR}/${host}.txt"
            printf '### %s\n\n' "$host"
            if [ "${statuses[$index]}" -ne 0 ]; then
                printf '**SSH failed.**\n\n```text\n%s\n```\n\n' "$(cat "$file")"
                continue
            fi
            printf -- '- Hostname: `%s`\n' "$(single_line HOSTNAME "$file")"
            printf -- '- CPU model: `%s`\n' "$(single_line CPU_MODEL "$file")"
            printf -- '- Online CPUs: `%s`\n' "$(single_line CPU_COUNT "$file")"
            printf -- '- Memory bytes: `%s`\n' "$(single_line MEMORY_BYTES "$file")"
            printf -- '- GPUs: `%s`\n' "$(single_line GPUS "$file")"
            printf -- '- NVIDIA driver: `%s`\n' "$(single_line NVIDIA_DRIVER "$file")"
            printf -- '- NUMA nodes: `%s`\n' "$(single_line NUMA_NODES "$file")"
            printf -- '- Virtualization: `%s`\n' "$(single_line VIRTUALIZATION "$file")"
            printf -- '- Watchdog devices: `%s`\n' "$(single_line WATCHDOGS "$file")"
            printf -- '- systemd available: `%s`\n' "$(single_line SYSTEMD "$file")"
            printf -- '- Compiler: `%s`\n' "$(single_line COMPILER "$file")"
            printf -- '- Uptime: `%s`\n\n' "$(single_line UPTIME "$file")"
            write_detail 'Operating system' OS_RELEASE "$file"
            write_detail 'CPU topology' LSCPU "$file"
            write_detail 'Memory' MEMORY "$file"
            write_detail 'NUMA' NUMA "$file"
            write_detail 'Block storage' STORAGE "$file"
            write_detail 'Mounted filesystems' FILESYSTEMS "$file"
            write_detail 'Watchdog' WATCHDOG "$file"
            write_detail 'GPU' GPU "$file"
            write_detail 'Network' NETWORK "$file"
            write_detail 'Process and memory limits' LIMITS "$file"
        done
    } >"$output"

    printf 'Wrote %s\n' "$output"
}

main "$@"
