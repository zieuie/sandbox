#!/usr/bin/env bash
# Give a worker its wired data-network address, the way .101-.108 have it ("King Hamming wired":
# 10.203.0.<last octet>/24 on the 1 Gb/s switch, manual, no default route, IPv6 off), and stop the
# netplan DHCP profile on that port from fighting it. Needs sudo on the HOST.
# Usage: add_wired_worker.sh HOST        (HOST = 192.168.4.N; the wired address becomes 10.203.0.N)
set -euo pipefail
host=${1:?usage: add_wired_worker.sh 192.168.4.N}
n=${host##*.}
ssh -o BatchMode=yes "$host" "sudo -n bash -s" <<REMOTE
set -euo pipefail
dev=\$(nmcli -t -f DEVICE,TYPE device | awk -F: '\$2=="ethernet"{print \$1; exit}')
[ -n "\$dev" ] || { echo "no ethernet device"; exit 1; }
if ! nmcli -t -f NAME connection show | grep -qx "King Hamming wired"; then
  nmcli connection add type ethernet con-name "King Hamming wired" ifname "\$dev" \
    ipv4.method manual ipv4.addresses 10.203.0.$n/24 ipv4.never-default yes \
    ipv6.method disabled connection.autoconnect yes
fi
# the generated DHCP profile on the same port: keep it from autoconnecting (evermore does the same)
nmcli -t -f NAME connection show | grep -x "netplan-\$dev" >/dev/null && nmcli connection modify "netplan-\$dev" connection.autoconnect no || true
nmcli connection up "King Hamming wired"
ip -br addr show "\$dev"
REMOTE
echo "wired up on $host; test from merlin: ping -c2 10.203.0.$n"
