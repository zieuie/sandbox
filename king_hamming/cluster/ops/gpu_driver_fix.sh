#!/usr/bin/env bash
# Fix NVIDIA drivers on fleet nodes; diagnosis in docs/GPU.md.
#
#   gpu_driver_fix.sh pellinore     install signed driver 580.178 on pellinore (.152), then reboot it
#   gpu_driver_fix.sh mok HOST...   queue Secure Boot key enrollment on P600 workers, then reboot them
#   gpu_driver_fix.sh check HOST... show whether each host's GPU is usable
#
# Run interactively from merlin: sudo prompts for your password on each host.
# Rebooted nodes need their agent relaunched afterwards (see the end of this script's output).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROBE="$ROOT/cuda/kh_cuda_probe"

host() { case "$1" in *.*) echo "$1" ;; *) echo "192.168.4.$1" ;; esac; }

case "${1:-}" in
pellinore)
    h=192.168.4.152
    echo "== installing Canonical-signed NVIDIA 580 (no DKMS, no X driver) on $h"
    ssh -t "$h" 'sudo apt-get install -y --no-install-recommends linux-modules-nvidia-580-generic nvidia-headless-no-dkms-580 nvidia-utils-580'
    echo "== rebooting $h (nouveau holds the GPU until then)"
    ssh -t "$h" 'sudo systemctl reboot' || true
    ;;
mok)
    shift
    [ $# -gt 0 ] || { echo "usage: $0 mok HOST..." >&2; exit 1; }
    for arg in "$@"; do
        h=$(host "$arg")
        echo "== $h: queueing enrollment of its DKMS signing key"
        echo "   mokutil asks for a one-time password; you type it again at the console after reboot."
        ssh -t "$h" 'sudo mokutil --import /var/lib/shim-signed/mok/MOK.der && sudo systemctl reboot' || true
        echo "   At $h's console: blue 'Perform MOK management' screen -> Enroll MOK -> Continue -> Yes -> password -> Reboot."
        echo "   (The screen times out after ~10 s and boots without enrolling; if you miss it, rerun this.)"
    done
    ;;
check)
    shift
    for arg in "$@"; do
        h=$(host "$arg")
        printf '%s: ' "$h"
        scp -q "$PROBE" "$h:/tmp/kh_cuda_probe" && ssh "$h" '/tmp/kh_cuda_probe 2>&1; rm -f /tmp/kh_cuda_probe; echo " signer=$(modinfo -F signer nvidia 2>/dev/null)"'
    done
    ;;
*)
    sed -n '2,9p' "$0"
    exit 1
    ;;
esac
cat <<'EOF'

After a node is back up with a working GPU (check with: gpu_driver_fix.sh check HOST),
its agent must be relaunched so it registers the GPU. Ask Claude to do it, or run
upgrade-workers after a drain.
EOF
