#!/usr/bin/env bash
# Install, inspect or remove the standalone Wi-Fi watchdog on machines over SSH (needs sudo there).
# Usage: install_wifi_watchdog.sh install|status|uninstall HOST...
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
action="${1:-}"; shift || true
[ -n "$action" ] && [ $# -gt 0 ] || { echo "usage: $0 install|status|uninstall HOST..." >&2; exit 2; }
for host in "$@"; do
  echo "== $host"
  case "$action" in
    install)
      scp -q "$here/wifi_watchdog.py" "$here/wifi-watchdog.service" "$here/wifi-watchdog.timer" "$host:/tmp/"
      ssh "$host" 'sudo -n install -m 0755 /tmp/wifi_watchdog.py /usr/local/sbin/wifi-watchdog &&
        sudo -n install -m 0644 /tmp/wifi-watchdog.service /tmp/wifi-watchdog.timer /etc/systemd/system/ &&
        rm -f /tmp/wifi_watchdog.py /tmp/wifi-watchdog.service /tmp/wifi-watchdog.timer &&
        sudo -n systemctl daemon-reload && sudo -n systemctl enable --now wifi-watchdog.timer &&
        sudo -n systemctl start wifi-watchdog.service && systemctl is-active wifi-watchdog.timer' ;;
    status)
      ssh "$host" 'systemctl is-enabled wifi-watchdog.timer; systemctl is-active wifi-watchdog.timer;
        sudo -n journalctl -u wifi-watchdog.service -n 5 --no-pager -o short-iso | grep -v "^--" || true' ;;
    uninstall)
      ssh "$host" 'sudo -n systemctl disable --now wifi-watchdog.timer || true;
        sudo -n rm -f /usr/local/sbin/wifi-watchdog /etc/systemd/system/wifi-watchdog.service /etc/systemd/system/wifi-watchdog.timer;
        sudo -n rm -rf /run/wifi-watchdog; sudo -n systemctl daemon-reload' ;;
    *) echo "unknown action $action" >&2; exit 2 ;;
  esac
done
