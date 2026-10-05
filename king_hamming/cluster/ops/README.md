# Pellinore network recovery

> **Retired (2026-10-04).** Pellinore was retired and powered off, so this watchdog no longer runs anywhere. Kept for reference: the same recovery approach is one of the options in [../../docs/NETWORK_OUTAGE_2026-10-02.md](../../docs/NETWORK_OUTAGE_2026-10-02.md), and the GPU driver notes below still apply to the other machines.

Pellinore (`192.168.4.152`) runs `pellinore-network-watchdog.timer` once per minute.
The oneshot service pings the gateway (`192.168.4.1`) and two cluster peers
(`.151` and `.101`) through `wlp59s0`. It restarts only NetworkManager after
three consecutive failed checks and a second confirmation ten seconds later.
Attempts are limited to one per ten minutes. The watchdog does not restart the
machine or the King Hamming agent, and it stays quiet while any peer responds.

The deployed files are `/usr/local/sbin/pellinore-network-watchdog` and the
service and timer units in `/etc/systemd/system/`. Check them with:

```sh
ssh 192.168.4.152 'systemctl status pellinore-network-watchdog.timer'
ssh 192.168.4.152 'sudo journalctl -u pellinore-network-watchdog.service -n 50 --no-pager'
ssh 192.168.4.152 'sudo cat /run/pellinore-network-watchdog/failures'
```

Disable automatic recovery with `sudo systemctl disable --now
pellinore-network-watchdog.timer` on Pellinore. The check is deliberately tied
to its current interface and LAN addresses; update those constants if its
network setup changes.

The 2026-10-02 outage that took five *other* workers offline for 4.5 hours has the
same failure mode this watchdog recovers from; see
[NETWORK_OUTAGE_2026-10-02.md](../../docs/NETWORK_OUTAGE_2026-10-02.md).

`gpu_driver_fix.sh` repairs NVIDIA drivers on fleet nodes (Secure Boot key enrollment on the
P600 workers, a fresh driver install on pellinore); see [GPU.md](../../docs/GPU.md). The drivers
are installed everywhere now, so its read-only `check HOST...` mode is the useful part.
