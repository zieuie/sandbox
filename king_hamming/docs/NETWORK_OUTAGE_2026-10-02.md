# Wi-Fi outage, 2026-10-02 03:07 CDT

**Status (2026-10-04):** diagnosed; none of the fixes below has been applied. Since then every
machine got a wired link (`10.203.0.X`), but only data transfers use it: the leader's control
traffic, SSH and the default route still go over Wi-Fi, so this failure mode can still take
workers offline. Moving the agents' leader address to the wired network would remove it (see
[CONTINUOUS_CAMPAIGN.md](CONTINUOUS_CAMPAIGN.md#wired-data-network)). The NVIDIA driver now
loads at boot ([GPU.md](GPU.md)), but the agents still don't start at boot: after a reboot,
relaunch them (`launch_dp.py resume-workers` or `upgrade-worker-rolling`).

## Recurrence, 2026-10-05 21:43

The same thing happened again. At 21:43:55 the access point `c0:6f:98:de:9e:07` switched to DFS
channel 104 (5520 MHz). fearless (`.101`), red (`.102`) and midnights (`.106`) lost Wi-Fi, failed
re-authentication with `no-secrets` at 21:45:58, and stayed off. The other machines rode it out.

- **Effect:** for about 13 minutes those three didn't heartbeat. Their 12 running tiles were
  re-leased elsewhere. Because copies on silent machines don't count, about 3,600 of 31⁷'s
  finished tiles showed as "complete, under-replicated" (light green) on the DP tiles page.
- **Recovery (21:57):** over the wired network, using each machine's known host key
  (`ssh -o HostKeyAlias=192.168.4.10X 10.203.0.10X`), ran
  `sudo -n nmcli connection up "The Promised LAN 1" ifname wlp3s0` on each. All three
  reconnected at once with the stored key, and their agents resumed without a restart.
  Every tile was durable again within a minute.
- **Still unfixed:** the fix options below. Router off DFS channels, or the watchdog on every
  node, would have prevented this.

## Impact

Five workers lost the network at 03:07 and stayed offline until a person
intervened about 4.5 hours later:

- `.101` fearless: reconnected at 07:43 when a desktop session appeared.
- `.103` lover, `.106` midnights, `.107` poets, `.108` showgirl: physically
  rebooted between 07:30 and 07:42.

The campaign kept running on `.102`, `.104`, `.105`, Merlin and Pellinore, at
about half capacity. Neither DP root lost any data or had a failed tile.

The leader's last heartbeats from the five machines were 03:06:58–03:07:05, and
each agent log ends with `Network is unreachable`.

## Cause

Every node, Merlin and Pellinore included, connects over Wi-Fi to "The Promised
LAN" (NetworkManager connection "The Promised LAN [1|2]"). None has a wired
link.

1. **The access point changed channel.** At 03:07:06, AP `c0:6f:98:de:9e:07`
   announced a switch on 5560 MHz (channel 112). That is a DFS channel, which
   the AP must vacate when it detects radar. All eight Intel-Wi-Fi workers lost
   it two seconds later:

   ```
   03:07:06 wpa_supplicant: wlp3s0: CTRL-EVENT-STARTED-CHANNEL-SWITCH freq=5560 ...
   03:07:08 kernel: iwlwifi: No beacon heard and the time event is over already...
   03:07:08 kernel: wlp3s0: Connection to AP c0:6f:98:de:9e:07 lost
   ```

2. **Roaming decided which nodes survived.** Most nodes then tried AP
   `c0:6f:98:e1:42:26` on 2.4 GHz.
   - Survivors (`.102`, `.104`, `.105`): that attempt failed quickly, or they
     joined `…de:9e:06`. They were back on `…de:9e:07` within 3–6 s.
   - Dead nodes (`.101`, `.103`, `.106`, `.107`): they associated with
     `…e1:42:26`, but the WPA 4-way handshake then timed out:

     ```
     03:07:16 kernel: wlp3s0: deauthenticated from c0:6f:98:e1:42:26 (Reason: 15=4WAY_HANDSHAKE_TIMEOUT)
     03:07:16 NetworkManager: device (wlp3s0): state change: activated -> need-auth (reason 'supplicant-disconnect')
     ```

3. **NetworkManager gave up permanently.** It treats a handshake timeout as a
   possibly wrong password, stops trusting the saved key, and asks a secrets
   agent (a logged-in desktop) for a new one. No one was logged in:

   ```
   03:07:16 gnome-shell: polkitAuthenticationAgent: Failed to show modal dialog ...
   03:09:16 NetworkManager: device (wlp3s0): no secrets: No agents were available for this request.
   03:09:16 NetworkManager: device (wlp3s0): Activation: failed for connection 'The Promised LAN 1'
   ```

   After a `no-secrets` failure, NetworkManager blocks autoconnect until an
   agent registers. On `.101` that happened at 07:43:34, and it then
   reconnected immediately with the stored key:

   ```
   07:43:34 NetworkManager: agent-manager: agent[...org.gnome.Shell.NetworkAgent/1000]: agent registered
   07:43:34 NetworkManager: ... connection 'The Promised LAN 1' has security, and secrets exist.
   ```

The password is stored system-wide on all ten machines
(`802-11-wireless-security.psk-flags=0`), so this is NetworkManager's normal
behaviour, not a misconfiguration.

Merlin's Wi-Fi card followed the channel switch in place
(`CTRL-EVENT-CHANNEL-SWITCH`) and never disconnected. Pellinore runs
[`pellinore-network-watchdog`](../cluster/ops/README.md), which restarts
NetworkManager after failed checks and so clears this state. `.108`'s journal
from before its reboot was not retained, so its sequence is inferred from the
identical 03:07 timing.

## Problems the reboots exposed

- **Agents do not start at boot.** No unit exists. The four rebooted agents were
  relaunched by hand at about 07:48 with `dp_solver/launch_dp.launch_worker`
  (same root and runtime version), and the manifest records the new PIDs.
- **The NVIDIA driver does not load at boot** on the rebooted nodes. Module
  580.173.02 is installed, but the boot log shows no load attempt, so GPU work
  ([GPU.md](GPU.md)) would silently fall back to CPU there.

## Fix options

None of these is applied yet. All except the first need sudo on the nodes.

1. **Router:** keep the 5 GHz network off DFS channels (36–48 or 149–165). This
   removes the trigger for every machine at once.
2. **Watchdog on every node:** roll out Pellinore's watchdog, adapted to
   `wlp3s0` and suitable peer addresses. Restarting NetworkManager after
   repeated failed checks clears the `no-secrets` block.
3. **Investigate AP `c0:6f:98:e1:42:26`:** find out why it fails the 4-way
   handshake (weak signal, a mesh-satellite or band-steering issue).
4. **Wired Ethernet** where possible; `.105` has an unused `enp0s31f6` port.
5. **systemd units** to start the King Hamming agent and load `nvidia` at boot.

## Commands used (read-only)

```sh
ssh 192.168.4.101 'journalctl --since "2026-10-02 02:50" --until "2026-10-02 07:50" | grep -E "wlp|NetworkManager|wpa_supplicant"'
ssh 192.168.4.107 'journalctl -b -1 --since "2026-10-02 03:07" --until "2026-10-02 03:12"'   # rebooted nodes: previous boot
ssh 192.168.4.10X 'nmcli -g 802-11-wireless-security.psk-flags con show "The Promised LAN 1"'
```
