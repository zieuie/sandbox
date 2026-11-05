#!/usr/bin/env bash
# Stopgap for CVE-2026-98432 (remote use-after-free in the kernel's IPv4 TCP handshake) until the
# fixed kernel (7.0.0-43) is out: drop incoming IPv4 TCP packets that carry IP options, which the
# exploit needs. Ubuntu's suggested mitigation; it lowers the risk but does not replace the kernel
# fix. Persists via /etc/nftables.conf + nftables.service (enabled, not restarted, so nothing else
# in the live ruleset is touched). Needs sudo on HOST. Idempotent.
# Usage: cve_2026_98432_mitigation.sh install|status|uninstall HOST...
set -euo pipefail
action="${1:-}"; shift || true
[ -n "$action" ] && [ $# -gt 0 ] || { echo "usage: $0 install|status|uninstall HOST..." >&2; exit 2; }
for host in "$@"; do
  echo "== $host"
  case "$action" in
    install)
      ssh -o BatchMode=yes "$host" "sudo -n bash -s" <<'REMOTE'
set -euo pipefail
cat > /etc/cve-2026-98432.nft <<'NFT'
table inet cve_2026_98432 {
  chain input {
    type filter hook input priority -310; policy accept;
    ip protocol tcp ip hdrlength > 5 counter drop
  }
}
NFT
chmod 0644 /etc/cve-2026-98432.nft
nft -c -f /etc/cve-2026-98432.nft
nft list table inet cve_2026_98432 >/dev/null 2>&1 && nft delete table inet cve_2026_98432
nft -f /etc/cve-2026-98432.nft
grep -qx 'include "/etc/cve-2026-98432.nft"' /etc/nftables.conf ||
  echo 'include "/etc/cve-2026-98432.nft"' >> /etc/nftables.conf
nft -c -f /etc/nftables.conf
systemctl enable --quiet nftables.service
echo "$(hostname): installed; nftables $(systemctl is-enabled nftables.service)"
REMOTE
      ;;
    status)
      ssh -o BatchMode=yes "$host" 'echo "$(hostname) $(uname -r) nftables=$(systemctl is-enabled nftables.service)" \
        "include=$(grep -cx "include \"/etc/cve-2026-98432.nft\"" /etc/nftables.conf)" \
        "rule: $(sudo -n nft list table inet cve_2026_98432 2>/dev/null | grep -o "counter packets [0-9]* bytes [0-9]*" || echo MISSING)"' ;;
    uninstall)
      ssh -o BatchMode=yes "$host" 'sudo -n sed -i "\|^include \"/etc/cve-2026-98432.nft\"$|d" /etc/nftables.conf;
        sudo -n nft delete table inet cve_2026_98432 2>/dev/null || true; sudo -n rm -f /etc/cve-2026-98432.nft;
        echo "$(hostname): removed (nftables.service left $(systemctl is-enabled nftables.service))"' ;;
    *) echo "unknown action $action" >&2; exit 2 ;;
  esac
done
