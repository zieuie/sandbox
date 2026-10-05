# king_hamming cluster inventory

Generated: `2026-10-05T00:23:47-05:00`

Command: `inventory_cluster.sh --run`

This report contains hardware and operating-system facts needed to size
resident fields, DP tiles, matching state, checkpoints, and watchdog setup.

Naming note: the leader machine is called **merlin**, but its transplanted SSD still
reports the hostname `uther`. Both names refer to `192.168.4.151`.

## Fleet notes (2026-10-04)

These notes are written by hand; everything from "Summary" down is generated.

- **Ten machines:** the eight `.101`–`.108` mini-PCs (i7-7700T, 15.5 GiB, Quadro P600),
  merlin `.151` (MSI GE76 laptop, i7-11800H, 38.9 GiB, RTX 3060 Laptop) and gawain `.156`
  (ThinkPad P1 Gen 2, i7-9850H, 7.4 GiB, Quadro T1000). GPUs appear in the summary and in
  each machine's GPU section; see [GPU.md](GPU.md) for how the cluster uses them.
- **pellinore `.152` was retired on 2026-10-04** and powered off; see
  [MACHINE_CONTRIBUTIONS.md](MACHINE_CONTRIBUTIONS.md). Its last inventory is in this
  file's git history.
- **Networks:** every machine is on Wi-Fi (`192.168.4.0/22`: default route, SSH, leader
  control traffic) and on a 1 Gb/s switch (`10.203.0.X/24`: data transfers). See
  [CONTINUOUS_CAMPAIGN.md](CONTINUOUS_CAMPAIGN.md#wired-data-network).
- **No hardware watchdog** is visible on any machine (`/dev/watchdog*` is absent).
- **Toolchain:** binaries are built on merlin and deployed as a bundle; workers don't need a
  compiler or a CUDA toolkit.
- **Drive health:** see [TILE_SCRATCH_RAM.md](TILE_SCRATCH_RAM.md#drive-health-2026-10-04-after-the-rollout).

## Summary

| Address | Status | Hostname | Architecture | CPUs | Memory | GPU | NUMA | Watchdog | OS/kernel |
| --- | --- | --- | --- | ---: | ---: | --- | ---: | --- | --- |
| 192.168.4.101 | ok | fearless | x86_64 | 8 | 15.5 GiB | Quadro P600 (2.0 GiB) | 1 | none | Linux 7.0.0-38-generic |
| 192.168.4.102 | ok | red | x86_64 | 8 | 15.5 GiB | Quadro P600 (2.0 GiB) | 1 | none | Linux 7.0.0-38-generic |
| 192.168.4.103 | ok | lover | x86_64 | 8 | 15.5 GiB | Quadro P600 (2.0 GiB) | 1 | none | Linux 7.0.0-38-generic |
| 192.168.4.104 | ok | folklore | x86_64 | 8 | 15.5 GiB | Quadro P600 (2.0 GiB) | 1 | none | Linux 7.0.0-38-generic |
| 192.168.4.105 | ok | evermore | x86_64 | 8 | 15.5 GiB | Quadro P600 (2.0 GiB) | 1 | none | Linux 7.0.0-38-generic |
| 192.168.4.106 | ok | midnights | x86_64 | 8 | 15.5 GiB | Quadro P600 (2.0 GiB) | 1 | none | Linux 7.0.0-38-generic |
| 192.168.4.107 | ok | poets | x86_64 | 8 | 15.5 GiB | Quadro P600 (2.0 GiB) | 1 | none | Linux 7.0.0-38-generic |
| 192.168.4.108 | ok | showgirl | x86_64 | 8 | 15.5 GiB | Quadro P600 (2.0 GiB) | 1 | none | Linux 7.0.0-38-generic |
| 192.168.4.151 | ok | uther | x86_64 | 16 | 38.9 GiB | NVIDIA GeForce RTX 3060 Laptop GPU (6.0 GiB) | 1 | none | Linux 7.0.0-38-generic |
| 192.168.4.156 | ok | gawain | x86_64 | 12 | 7.4 GiB | Quadro T1000 (4.0 GiB) | 1 | none | Linux 7.0.0-38-generic |

## Machine details

### 192.168.4.101

- Hostname: `fearless`
- CPU model: `Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz`
- Online CPUs: `8`
- Memory bytes: `16646676480`
- GPUs: `Quadro P600 (2.0 GiB)`
- NVIDIA driver: `580.178.04`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: ``
- Uptime: `up 1 day, 23 hours, 16 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.5 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.5 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=noble
LOGO=ubuntu-logo
```

#### CPU topology

```text
Architecture:                            x86_64
CPU op-mode(s):                          32-bit, 64-bit
Address sizes:                           39 bits physical, 48 bits virtual
Byte Order:                              Little Endian
CPU(s):                                  8
On-line CPU(s) list:                     0-7
Vendor ID:                               GenuineIntel
Model name:                              Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz
CPU family:                              6
Model:                                   158
Thread(s) per core:                      2
Core(s) per socket:                      4
Socket(s):                               1
Stepping:                                9
CPU(s) scaling MHz:                      95%
CPU max MHz:                             3800.0000
CPU min MHz:                             800.0000
BogoMIPS:                                5799.77
Flags:                                   fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl smx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault epb pti ssbd ibrs ibpb stibp fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp md_clear flush_l1d arch_capabilities
L1d cache:                               128 KiB (4 instances)
L1i cache:                               128 KiB (4 instances)
L2 cache:                                1 MiB (4 instances)
L3 cache:                                8 MiB (1 instance)
NUMA node(s):                            1
NUMA node0 CPU(s):                       0-7
Vulnerability Gather data sampling:      Vulnerable
Vulnerability Ghostwrite:                Not affected
Vulnerability Indirect target selection: Not affected
Vulnerability Itlb multihit:             KVM: Mitigation: VMX unsupported
Vulnerability L1tf:                      Mitigation; PTE Inversion
Vulnerability Mds:                       Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Meltdown:                  Mitigation; PTI
Vulnerability Mmio stale data:           Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Old microcode:             Not affected
Vulnerability Reg file data sampling:    Not affected
Vulnerability Retbleed:                  Mitigation; IBRS
Vulnerability Spec rstack overflow:      Not affected
Vulnerability Spec store bypass:         Mitigation; Speculative Store Bypass disabled via prctl
Vulnerability Spectre v1:                Mitigation; usercopy/swapgs barriers and __user pointer sanitization
Vulnerability Spectre v2:                Mitigation; IBRS; IBPB conditional; STIBP conditional; RSB filling; PBRSB-eIBRS Not affected; BHI Not affected
Vulnerability Srbds:                     Mitigation; Microcode
Vulnerability Tsa:                       Not affected
Vulnerability Tsx async abort:           Mitigation; TSX disabled
Vulnerability Vmscape:                   Mitigation; IBPB before exit to userspace
```

#### Memory

```text
               total        used        free      shared  buff/cache   available
Mem:            15Gi       1.6Gi       3.5Gi        92Mi        10Gi        13Gi
Swap:          4.0Gi       4.0Ki       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15875 MB
node 0 free: 3535 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop     4K    0 squashfs /snap/bare/5                        
loop1       loop    74M    0 squashfs /snap/core22/2411                   
loop2       loop  66.8M    0 squashfs /snap/core24/1587                   
loop3       loop  66.8M    0 squashfs /snap/core24/2124                   
loop4       loop    74M    0 squashfs /snap/core22/2955                   
loop5       loop 261.4M    0 squashfs /snap/firefox/8969                  
loop6       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop7       loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop8       loop  12.7M    0 squashfs /snap/firmware-updater/258          
loop9       loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop10      loop 531.5M    0 squashfs /snap/gnome-42-2204/263             
loop11      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop12      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop13      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop14      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop15      loop  11.8M    0 squashfs /snap/snap-store/1427               
loop16      loop  50.3M    0 squashfs /snap/snapd/27738                   
loop17      loop  44.7M    0 squashfs /snap/snapd/28254                   
loop18      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
loop19      loop   828K    0 squashfs /snap/snapd-desktop-integration/391 
loop20      loop 615.3M    0 squashfs /snap/gnome-46-2404/168             
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLW256HEHP-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   39G  182G  18% /
efivarfs       efivarfs  256K   62K  190K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### GPU

```text
index, name, memory.total [MiB], driver_version, compute_cap, pci.bus_id
0, Quadro P600, 2048 MiB, 580.178.04, 6.1, 00000000:01:00.0
Display controllers (lspci):
00:02.0 Display controller: Intel Corporation HD Graphics 630 (rev 04)
01:00.0 VGA compatible controller: NVIDIA Corporation GP107GL [Quadro P600] (rev a1)
```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        UP             10.203.0.101/24 
wlp3s0           UP             192.168.4.101/22 fd47:fae1:3712:1:d51b:b717:5d24:f70b/64 fd47:fae1:3712:1:f21b:56ae:e2cc:5178/64 fd47:fae1:3712:1:e7c7:e08a:4c99:515c/64 fe80::667b:4a36:44f9:87ac/64 
default via 192.168.4.1 dev wlp3s0 proto dhcp src 192.168.4.101 metric 600 
```

#### Process and memory limits

```text
page_size=4096
open_files_soft=1024
open_files_hard=1048576
transparent_hugepages=always [madvise] never
```

### 192.168.4.102

- Hostname: `red`
- CPU model: `Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz`
- Online CPUs: `8`
- Memory bytes: `16647053312`
- GPUs: `Quadro P600 (2.0 GiB)`
- NVIDIA driver: `580.178.04`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: `cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
- Uptime: `up 1 day, 23 hours, 16 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.5 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.5 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=noble
LOGO=ubuntu-logo
```

#### CPU topology

```text
Architecture:                            x86_64
CPU op-mode(s):                          32-bit, 64-bit
Address sizes:                           39 bits physical, 48 bits virtual
Byte Order:                              Little Endian
CPU(s):                                  8
On-line CPU(s) list:                     0-7
Vendor ID:                               GenuineIntel
Model name:                              Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz
CPU family:                              6
Model:                                   158
Thread(s) per core:                      2
Core(s) per socket:                      4
Socket(s):                               1
Stepping:                                9
CPU(s) scaling MHz:                      95%
CPU max MHz:                             3800.0000
CPU min MHz:                             800.0000
BogoMIPS:                                5799.77
Flags:                                   fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl smx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault epb pti ssbd ibrs ibpb stibp fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp md_clear flush_l1d arch_capabilities
L1d cache:                               128 KiB (4 instances)
L1i cache:                               128 KiB (4 instances)
L2 cache:                                1 MiB (4 instances)
L3 cache:                                8 MiB (1 instance)
NUMA node(s):                            1
NUMA node0 CPU(s):                       0-7
Vulnerability Gather data sampling:      Vulnerable
Vulnerability Ghostwrite:                Not affected
Vulnerability Indirect target selection: Not affected
Vulnerability Itlb multihit:             KVM: Mitigation: VMX unsupported
Vulnerability L1tf:                      Mitigation; PTE Inversion
Vulnerability Mds:                       Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Meltdown:                  Mitigation; PTI
Vulnerability Mmio stale data:           Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Old microcode:             Not affected
Vulnerability Reg file data sampling:    Not affected
Vulnerability Retbleed:                  Mitigation; IBRS
Vulnerability Spec rstack overflow:      Not affected
Vulnerability Spec store bypass:         Mitigation; Speculative Store Bypass disabled via prctl
Vulnerability Spectre v1:                Mitigation; usercopy/swapgs barriers and __user pointer sanitization
Vulnerability Spectre v2:                Mitigation; IBRS; IBPB conditional; STIBP conditional; RSB filling; PBRSB-eIBRS Not affected; BHI Not affected
Vulnerability Srbds:                     Mitigation; Microcode
Vulnerability Tsa:                       Not affected
Vulnerability Tsx async abort:           Mitigation; TSX disabled
Vulnerability Vmscape:                   Mitigation; IBPB before exit to userspace
```

#### Memory

```text
               total        used        free      shared  buff/cache   available
Mem:            15Gi       2.0Gi       1.6Gi       257Mi        12Gi        13Gi
Swap:          4.0Gi       300Ki       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15875 MB
node 0 free: 1635 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop     4K    0 squashfs /snap/bare/5                        
loop1       loop    74M    0 squashfs /snap/core22/2411                   
loop2       loop  66.8M    0 squashfs /snap/core24/2124                   
loop3       loop    74M    0 squashfs /snap/core22/2955                   
loop4       loop  66.8M    0 squashfs /snap/core24/1587                   
loop5       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop6       loop 262.2M    0 squashfs /snap/firefox/8995                  
loop7       loop 505.1M    0 squashfs /snap/gnome-42-2204/176             
loop8       loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop9       loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop10      loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop11      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop12      loop  12.7M    0 squashfs /snap/firmware-updater/258          
loop13      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop14      loop  11.8M    0 squashfs /snap/snap-store/1427               
loop15      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop16      loop  44.7M    0 squashfs /snap/snapd/28254                   
loop17      loop  50.3M    0 squashfs /snap/snapd/27738                   
loop18      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
loop19      loop   828K    0 squashfs /snap/snapd-desktop-integration/391 
loop20      loop   402M    0 squashfs /snap/mesa-2404/1839                
loop21      loop 615.3M    0 squashfs /snap/gnome-46-2404/168             
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLB256HAHQ-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   42G  179G  19% /
efivarfs       efivarfs  256K   62K  190K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### GPU

```text
index, name, memory.total [MiB], driver_version, compute_cap, pci.bus_id
0, Quadro P600, 2048 MiB, 580.178.04, 6.1, 00000000:01:00.0
Display controllers (lspci):
00:02.0 Display controller: Intel Corporation HD Graphics 630 (rev 04)
01:00.0 VGA compatible controller: NVIDIA Corporation GP107GL [Quadro P600] (rev a1)
```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        UP             10.203.0.102/24 
wlp3s0           UP             192.168.4.102/22 fd47:fae1:3712:1:833c:a119:fe7:aa94/64 fd47:fae1:3712:1:869b:3ea4:310e:e791/64 fd47:fae1:3712:1:3000:6102:9129:16bd/64 fe80::216a:519d:7903:5a16/64 
default via 192.168.4.1 dev wlp3s0 proto dhcp src 192.168.4.102 metric 600 
```

#### Process and memory limits

```text
page_size=4096
open_files_soft=1024
open_files_hard=1048576
transparent_hugepages=always [madvise] never
```

### 192.168.4.103

- Hostname: `lover`
- CPU model: `Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz`
- Online CPUs: `8`
- Memory bytes: `16647045120`
- GPUs: `Quadro P600 (2.0 GiB)`
- NVIDIA driver: `580.178.04`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: `cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
- Uptime: `up 1 day, 23 hours, 7 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.5 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.5 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=noble
LOGO=ubuntu-logo
```

#### CPU topology

```text
Architecture:                            x86_64
CPU op-mode(s):                          32-bit, 64-bit
Address sizes:                           39 bits physical, 48 bits virtual
Byte Order:                              Little Endian
CPU(s):                                  8
On-line CPU(s) list:                     0-7
Vendor ID:                               GenuineIntel
Model name:                              Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz
CPU family:                              6
Model:                                   158
Thread(s) per core:                      2
Core(s) per socket:                      4
Socket(s):                               1
Stepping:                                9
CPU(s) scaling MHz:                      21%
CPU max MHz:                             3800.0000
CPU min MHz:                             800.0000
BogoMIPS:                                5799.77
Flags:                                   fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl smx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault epb pti ssbd ibrs ibpb stibp fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp md_clear flush_l1d arch_capabilities
L1d cache:                               128 KiB (4 instances)
L1i cache:                               128 KiB (4 instances)
L2 cache:                                1 MiB (4 instances)
L3 cache:                                8 MiB (1 instance)
NUMA node(s):                            1
NUMA node0 CPU(s):                       0-7
Vulnerability Gather data sampling:      Vulnerable
Vulnerability Ghostwrite:                Not affected
Vulnerability Indirect target selection: Not affected
Vulnerability Itlb multihit:             KVM: Mitigation: VMX unsupported
Vulnerability L1tf:                      Mitigation; PTE Inversion
Vulnerability Mds:                       Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Meltdown:                  Mitigation; PTI
Vulnerability Mmio stale data:           Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Old microcode:             Not affected
Vulnerability Reg file data sampling:    Not affected
Vulnerability Retbleed:                  Mitigation; IBRS
Vulnerability Spec rstack overflow:      Not affected
Vulnerability Spec store bypass:         Mitigation; Speculative Store Bypass disabled via prctl
Vulnerability Spectre v1:                Mitigation; usercopy/swapgs barriers and __user pointer sanitization
Vulnerability Spectre v2:                Mitigation; IBRS; IBPB conditional; STIBP conditional; RSB filling; PBRSB-eIBRS Not affected; BHI Not affected
Vulnerability Srbds:                     Mitigation; Microcode
Vulnerability Tsa:                       Not affected
Vulnerability Tsx async abort:           Mitigation; TSX disabled
Vulnerability Vmscape:                   Mitigation; IBPB before exit to userspace
```

#### Memory

```text
               total        used        free      shared  buff/cache   available
Mem:            15Gi       1.9Gi       1.9Gi       322Mi        12Gi        13Gi
Swap:          4.0Gi       612Ki       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15875 MB
node 0 free: 1921 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop     4K    0 squashfs /snap/bare/5                        
loop1       loop    74M    0 squashfs /snap/core22/2955                   
loop2       loop    74M    0 squashfs /snap/core22/2411                   
loop3       loop  66.8M    0 squashfs /snap/core24/1587                   
loop4       loop  66.8M    0 squashfs /snap/core24/2124                   
loop5       loop  16.5M    0 squashfs /snap/firmware-updater/226          
loop6       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop7       loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop9       loop 261.4M    0 squashfs /snap/firefox/8969                  
loop10      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop11      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop12      loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop13      loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop14      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop15      loop  11.8M    0 squashfs /snap/snap-store/1427               
loop16      loop  48.4M    0 squashfs /snap/snapd/26382                   
loop17      loop  44.7M    0 squashfs /snap/snapd/28254                   
loop18      loop   576K    0 squashfs /snap/snapd-desktop-integration/343 
loop19      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
loop20      loop 531.5M    0 squashfs /snap/gnome-42-2204/263             
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLB256HAHQ-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   55G  167G  25% /
efivarfs       efivarfs  256K   61K  191K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### GPU

```text
index, name, memory.total [MiB], driver_version, compute_cap, pci.bus_id
0, Quadro P600, 2048 MiB, 580.178.04, 6.1, 00000000:01:00.0
Display controllers (lspci):
00:02.0 Display controller: Intel Corporation HD Graphics 630 (rev 04)
01:00.0 VGA compatible controller: NVIDIA Corporation GP107GL [Quadro P600] (rev a1)
```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        UP             10.203.0.103/24 
wlp3s0           UP             192.168.4.103/22 fd47:fae1:3712:1:3c9d:7f8c:1fd7:aa71/64 fd47:fae1:3712:1:bd8b:7119:6c27:5962/64 fd47:fae1:3712:1:3f59:e9e0:b454:b2c0/64 fe80::3fd9:5d5f:c644:dfb6/64 
default via 192.168.4.1 dev wlp3s0 proto dhcp src 192.168.4.103 metric 600 
```

#### Process and memory limits

```text
page_size=4096
open_files_soft=1024
open_files_hard=1048576
transparent_hugepages=always [madvise] never
```

### 192.168.4.104

- Hostname: `folklore`
- CPU model: `Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz`
- Online CPUs: `8`
- Memory bytes: `16646668288`
- GPUs: `Quadro P600 (2.0 GiB)`
- NVIDIA driver: `580.178.04`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: `cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
- Uptime: `up 1 day, 23 hours, 16 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.5 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.5 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=noble
LOGO=ubuntu-logo
```

#### CPU topology

```text
Architecture:                            x86_64
CPU op-mode(s):                          32-bit, 64-bit
Address sizes:                           39 bits physical, 48 bits virtual
Byte Order:                              Little Endian
CPU(s):                                  8
On-line CPU(s) list:                     0-7
Vendor ID:                               GenuineIntel
Model name:                              Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz
CPU family:                              6
Model:                                   158
Thread(s) per core:                      2
Core(s) per socket:                      4
Socket(s):                               1
Stepping:                                9
CPU(s) scaling MHz:                      86%
CPU max MHz:                             3800.0000
CPU min MHz:                             800.0000
BogoMIPS:                                5799.77
Flags:                                   fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl smx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault epb pti ssbd ibrs ibpb stibp fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp md_clear flush_l1d arch_capabilities
L1d cache:                               128 KiB (4 instances)
L1i cache:                               128 KiB (4 instances)
L2 cache:                                1 MiB (4 instances)
L3 cache:                                8 MiB (1 instance)
NUMA node(s):                            1
NUMA node0 CPU(s):                       0-7
Vulnerability Gather data sampling:      Vulnerable
Vulnerability Ghostwrite:                Not affected
Vulnerability Indirect target selection: Not affected
Vulnerability Itlb multihit:             KVM: Mitigation: VMX unsupported
Vulnerability L1tf:                      Mitigation; PTE Inversion
Vulnerability Mds:                       Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Meltdown:                  Mitigation; PTI
Vulnerability Mmio stale data:           Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Old microcode:             Not affected
Vulnerability Reg file data sampling:    Not affected
Vulnerability Retbleed:                  Mitigation; IBRS
Vulnerability Spec rstack overflow:      Not affected
Vulnerability Spec store bypass:         Mitigation; Speculative Store Bypass disabled via prctl
Vulnerability Spectre v1:                Mitigation; usercopy/swapgs barriers and __user pointer sanitization
Vulnerability Spectre v2:                Mitigation; IBRS; IBPB conditional; STIBP conditional; RSB filling; PBRSB-eIBRS Not affected; BHI Not affected
Vulnerability Srbds:                     Mitigation; Microcode
Vulnerability Tsa:                       Not affected
Vulnerability Tsx async abort:           Mitigation; TSX disabled
Vulnerability Vmscape:                   Mitigation; IBPB before exit to userspace
```

#### Memory

```text
               total        used        free      shared  buff/cache   available
Mem:            15Gi       2.0Gi       8.4Gi       313Mi       5.8Gi        13Gi
Swap:          4.0Gi       4.0Ki       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15875 MB
node 0 free: 8583 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop     4K    0 squashfs /snap/bare/5                        
loop1       loop    74M    0 squashfs /snap/core22/2411                   
loop2       loop    74M    0 squashfs /snap/core22/2955                   
loop3       loop  66.8M    0 squashfs /snap/core24/1587                   
loop4       loop 273.5M    0 squashfs /snap/firefox/8054                  
loop5       loop  66.8M    0 squashfs /snap/core24/2124                   
loop6       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop7       loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop8       loop  12.7M    0 squashfs /snap/firmware-updater/258          
loop9       loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop10      loop 531.5M    0 squashfs /snap/gnome-42-2204/263             
loop11      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop12      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop13      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop14      loop   402M    0 squashfs /snap/mesa-2404/1839                
loop15      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop16      loop  11.8M    0 squashfs /snap/snap-store/1427               
loop17      loop  50.3M    0 squashfs /snap/snapd/27738                   
loop18      loop  44.7M    0 squashfs /snap/snapd/28254                   
loop19      loop 615.3M    0 squashfs /snap/gnome-46-2404/168             
loop20      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
loop21      loop   828K    0 squashfs /snap/snapd-desktop-integration/391 
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLW256HEHP-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   32G  190G  15% /
efivarfs       efivarfs  256K   61K  191K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### GPU

```text
index, name, memory.total [MiB], driver_version, compute_cap, pci.bus_id
0, Quadro P600, 2048 MiB, 580.178.04, 6.1, 00000000:01:00.0
Display controllers (lspci):
00:02.0 Display controller: Intel Corporation HD Graphics 630 (rev 04)
01:00.0 VGA compatible controller: NVIDIA Corporation GP107GL [Quadro P600] (rev a1)
```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        UP             10.203.0.104/24 
wlp3s0           UP             192.168.4.104/22 fd47:fae1:3712:1:335a:21f6:8eff:ce45/64 fd47:fae1:3712:1:6d5:47a9:cff0:465f/64 fd47:fae1:3712:1:35ce:cc46:c60b:54aa/64 fe80::a7f4:38ca:7476:60f/64 
default via 192.168.4.1 dev wlp3s0 proto dhcp src 192.168.4.104 metric 600 
```

#### Process and memory limits

```text
page_size=4096
open_files_soft=1024
open_files_hard=1048576
transparent_hugepages=always [madvise] never
```

### 192.168.4.105

- Hostname: `evermore`
- CPU model: `Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz`
- Online CPUs: `8`
- Memory bytes: `16647045120`
- GPUs: `Quadro P600 (2.0 GiB)`
- NVIDIA driver: `580.178.04`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: ``
- Uptime: `up 1 day, 23 hours, 7 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.5 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.5 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=noble
LOGO=ubuntu-logo
```

#### CPU topology

```text
Architecture:                            x86_64
CPU op-mode(s):                          32-bit, 64-bit
Address sizes:                           39 bits physical, 48 bits virtual
Byte Order:                              Little Endian
CPU(s):                                  8
On-line CPU(s) list:                     0-7
Vendor ID:                               GenuineIntel
Model name:                              Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz
CPU family:                              6
Model:                                   158
Thread(s) per core:                      2
Core(s) per socket:                      4
Socket(s):                               1
Stepping:                                9
CPU(s) scaling MHz:                      26%
CPU max MHz:                             3800.0000
CPU min MHz:                             800.0000
BogoMIPS:                                5799.77
Flags:                                   fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl smx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault epb pti ssbd ibrs ibpb stibp fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp md_clear flush_l1d arch_capabilities
L1d cache:                               128 KiB (4 instances)
L1i cache:                               128 KiB (4 instances)
L2 cache:                                1 MiB (4 instances)
L3 cache:                                8 MiB (1 instance)
NUMA node(s):                            1
NUMA node0 CPU(s):                       0-7
Vulnerability Gather data sampling:      Vulnerable
Vulnerability Ghostwrite:                Not affected
Vulnerability Indirect target selection: Not affected
Vulnerability Itlb multihit:             KVM: Mitigation: VMX unsupported
Vulnerability L1tf:                      Mitigation; PTE Inversion
Vulnerability Mds:                       Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Meltdown:                  Mitigation; PTI
Vulnerability Mmio stale data:           Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Old microcode:             Not affected
Vulnerability Reg file data sampling:    Not affected
Vulnerability Retbleed:                  Mitigation; IBRS
Vulnerability Spec rstack overflow:      Not affected
Vulnerability Spec store bypass:         Mitigation; Speculative Store Bypass disabled via prctl
Vulnerability Spectre v1:                Mitigation; usercopy/swapgs barriers and __user pointer sanitization
Vulnerability Spectre v2:                Mitigation; IBRS; IBPB conditional; STIBP conditional; RSB filling; PBRSB-eIBRS Not affected; BHI Not affected
Vulnerability Srbds:                     Mitigation; Microcode
Vulnerability Tsa:                       Not affected
Vulnerability Tsx async abort:           Mitigation; TSX disabled
Vulnerability Vmscape:                   Mitigation; IBPB before exit to userspace
```

#### Memory

```text
               total        used        free      shared  buff/cache   available
Mem:            15Gi       2.2Gi       5.2Gi       340Mi       8.8Gi        13Gi
Swap:          4.0Gi       620Ki       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15875 MB
node 0 free: 5295 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop     4K    0 squashfs /snap/bare/5                        
loop1       loop    74M    0 squashfs /snap/core22/2411                   
loop2       loop    74M    0 squashfs /snap/core22/2955                   
loop3       loop  66.8M    0 squashfs /snap/core24/1587                   
loop4       loop  66.8M    0 squashfs /snap/core24/2124                   
loop5       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop6       loop 262.2M    0 squashfs /snap/firefox/8995                  
loop7       loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop8       loop  12.7M    0 squashfs /snap/firmware-updater/258          
loop9       loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop10      loop 531.5M    0 squashfs /snap/gnome-42-2204/263             
loop11      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop12      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop13      loop 615.3M    0 squashfs /snap/gnome-46-2404/168             
loop14      loop   402M    0 squashfs /snap/mesa-2404/1839                
loop15      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop16      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop17      loop  50.3M    0 squashfs /snap/snapd/27738                   
loop18      loop  11.8M    0 squashfs /snap/snap-store/1427               
loop19      loop  44.7M    0 squashfs /snap/snapd/28254                   
loop20      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
loop21      loop   828K    0 squashfs /snap/snapd-desktop-integration/391 
nvme0n1     disk 238.5G    0                                              Micron MTFDHBA256TDV
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   38G  184G  17% /
efivarfs       efivarfs  256K   62K  190K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### GPU

```text
index, name, memory.total [MiB], driver_version, compute_cap, pci.bus_id
0, Quadro P600, 2048 MiB, 580.178.04, 6.1, 00000000:01:00.0
Display controllers (lspci):
00:02.0 Display controller: Intel Corporation HD Graphics 630 (rev 04)
01:00.0 VGA compatible controller: NVIDIA Corporation GP107GL [Quadro P600] (rev a1)
```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        UP             10.203.0.105/24 
wlp3s0           UP             192.168.4.105/22 fd47:fae1:3712:1:9fc4:ed94:5c23:185d/64 fd47:fae1:3712:1:bf5a:da7e:66e2:89f/64 fd47:fae1:3712:1:95cd:8836:f2b0:2506/64 fe80::3a51:80ff:7411:ec5d/64 
default via 192.168.4.1 dev wlp3s0 proto dhcp src 192.168.4.105 metric 600 
```

#### Process and memory limits

```text
page_size=4096
open_files_soft=1024
open_files_hard=1048576
transparent_hugepages=always [madvise] never
```

### 192.168.4.106

- Hostname: `midnights`
- CPU model: `Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz`
- Online CPUs: `8`
- Memory bytes: `16650199040`
- GPUs: `Quadro P600 (2.0 GiB)`
- NVIDIA driver: `580.178.04`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: `cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
- Uptime: `up 1 day, 23 hours, 16 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.5 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.5 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=noble
LOGO=ubuntu-logo
```

#### CPU topology

```text
Architecture:                            x86_64
CPU op-mode(s):                          32-bit, 64-bit
Address sizes:                           39 bits physical, 48 bits virtual
Byte Order:                              Little Endian
CPU(s):                                  8
On-line CPU(s) list:                     0-7
Vendor ID:                               GenuineIntel
Model name:                              Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz
CPU family:                              6
Model:                                   158
Thread(s) per core:                      2
Core(s) per socket:                      4
Socket(s):                               1
Stepping:                                9
CPU(s) scaling MHz:                      21%
CPU max MHz:                             3800.0000
CPU min MHz:                             800.0000
BogoMIPS:                                5799.77
Flags:                                   fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl smx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault epb pti ssbd ibrs ibpb stibp fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp md_clear flush_l1d arch_capabilities
L1d cache:                               128 KiB (4 instances)
L1i cache:                               128 KiB (4 instances)
L2 cache:                                1 MiB (4 instances)
L3 cache:                                8 MiB (1 instance)
NUMA node(s):                            1
NUMA node0 CPU(s):                       0-7
Vulnerability Gather data sampling:      Vulnerable
Vulnerability Ghostwrite:                Not affected
Vulnerability Indirect target selection: Not affected
Vulnerability Itlb multihit:             KVM: Mitigation: VMX unsupported
Vulnerability L1tf:                      Mitigation; PTE Inversion
Vulnerability Mds:                       Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Meltdown:                  Mitigation; PTI
Vulnerability Mmio stale data:           Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Old microcode:             Not affected
Vulnerability Reg file data sampling:    Not affected
Vulnerability Retbleed:                  Mitigation; IBRS
Vulnerability Spec rstack overflow:      Not affected
Vulnerability Spec store bypass:         Mitigation; Speculative Store Bypass disabled via prctl
Vulnerability Spectre v1:                Mitigation; usercopy/swapgs barriers and __user pointer sanitization
Vulnerability Spectre v2:                Mitigation; IBRS; IBPB conditional; STIBP conditional; RSB filling; PBRSB-eIBRS Not affected; BHI Not affected
Vulnerability Srbds:                     Mitigation; Microcode
Vulnerability Tsa:                       Not affected
Vulnerability Tsx async abort:           Mitigation; TSX disabled
Vulnerability Vmscape:                   Mitigation; IBPB before exit to userspace
```

#### Memory

```text
               total        used        free      shared  buff/cache   available
Mem:            15Gi       1.9Gi       8.6Gi       331Mi       5.6Gi        13Gi
Swap:          4.0Gi       596Ki       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15878 MB
node 0 free: 8836 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop    74M    0 squashfs /snap/core22/2411                   
loop1       loop  66.8M    0 squashfs /snap/core24/2124                   
loop2       loop     4K    0 squashfs /snap/bare/5                        
loop3       loop  66.8M    0 squashfs /snap/core24/1587                   
loop4       loop    74M    0 squashfs /snap/core22/2955                   
loop5       loop 273.5M    0 squashfs /snap/firefox/8054                  
loop6       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop7       loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop8       loop  12.7M    0 squashfs /snap/firmware-updater/258          
loop9       loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop10      loop 531.5M    0 squashfs /snap/gnome-42-2204/263             
loop11      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop12      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop13      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop14      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop15      loop   402M    0 squashfs /snap/mesa-2404/1839                
loop16      loop  11.8M    0 squashfs /snap/snap-store/1427               
loop18      loop  50.3M    0 squashfs /snap/snapd/27738                   
loop19      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
loop20      loop   828K    0 squashfs /snap/snapd-desktop-integration/391 
loop21      loop  44.7M    0 squashfs /snap/snapd/28254                   
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLB256HAHQ-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   35G  187G  16% /
efivarfs       efivarfs  256K   59K  193K  24% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### GPU

```text
index, name, memory.total [MiB], driver_version, compute_cap, pci.bus_id
0, Quadro P600, 2048 MiB, 580.178.04, 6.1, 00000000:01:00.0
Display controllers (lspci):
00:02.0 Display controller: Intel Corporation HD Graphics 630 (rev 04)
01:00.0 VGA compatible controller: NVIDIA Corporation GP107GL [Quadro P600] (rev a1)
```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        UP             10.203.0.106/24 
wlp3s0           UP             192.168.4.106/22 fd47:fae1:3712:1:69a7:71a1:fb2c:285f/64 fd47:fae1:3712:1:7362:1243:9efe:baf4/64 fd47:fae1:3712:1:e699:5851:e159:9c76/64 fe80::fd32:40f4:b584:9cd8/64 
default via 192.168.4.1 dev wlp3s0 proto dhcp src 192.168.4.106 metric 600 
```

#### Process and memory limits

```text
page_size=4096
open_files_soft=1024
open_files_hard=1048576
transparent_hugepages=always [madvise] never
```

### 192.168.4.107

- Hostname: `poets`
- CPU model: `Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz`
- Online CPUs: `8`
- Memory bytes: `16646578176`
- GPUs: `Quadro P600 (2.0 GiB)`
- NVIDIA driver: `580.178.04`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: `cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
- Uptime: `up 1 day, 23 hours, 7 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.5 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.5 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=noble
LOGO=ubuntu-logo
```

#### CPU topology

```text
Architecture:                            x86_64
CPU op-mode(s):                          32-bit, 64-bit
Address sizes:                           39 bits physical, 48 bits virtual
Byte Order:                              Little Endian
CPU(s):                                  8
On-line CPU(s) list:                     0-7
Vendor ID:                               GenuineIntel
Model name:                              Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz
CPU family:                              6
Model:                                   158
Thread(s) per core:                      2
Core(s) per socket:                      4
Socket(s):                               1
Stepping:                                9
CPU(s) scaling MHz:                      87%
CPU max MHz:                             3800.0000
CPU min MHz:                             800.0000
BogoMIPS:                                5799.77
Flags:                                   fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl smx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault epb pti ssbd ibrs ibpb stibp fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp md_clear flush_l1d arch_capabilities
L1d cache:                               128 KiB (4 instances)
L1i cache:                               128 KiB (4 instances)
L2 cache:                                1 MiB (4 instances)
L3 cache:                                8 MiB (1 instance)
NUMA node(s):                            1
NUMA node0 CPU(s):                       0-7
Vulnerability Gather data sampling:      Vulnerable
Vulnerability Ghostwrite:                Not affected
Vulnerability Indirect target selection: Not affected
Vulnerability Itlb multihit:             KVM: Mitigation: VMX unsupported
Vulnerability L1tf:                      Mitigation; PTE Inversion
Vulnerability Mds:                       Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Meltdown:                  Mitigation; PTI
Vulnerability Mmio stale data:           Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Old microcode:             Not affected
Vulnerability Reg file data sampling:    Not affected
Vulnerability Retbleed:                  Mitigation; IBRS
Vulnerability Spec rstack overflow:      Not affected
Vulnerability Spec store bypass:         Mitigation; Speculative Store Bypass disabled via prctl
Vulnerability Spectre v1:                Mitigation; usercopy/swapgs barriers and __user pointer sanitization
Vulnerability Spectre v2:                Mitigation; IBRS; IBPB conditional; STIBP conditional; RSB filling; PBRSB-eIBRS Not affected; BHI Not affected
Vulnerability Srbds:                     Mitigation; Microcode
Vulnerability Tsa:                       Not affected
Vulnerability Tsx async abort:           Mitigation; TSX disabled
Vulnerability Vmscape:                   Mitigation; IBPB before exit to userspace
```

#### Memory

```text
               total        used        free      shared  buff/cache   available
Mem:            15Gi       2.0Gi       7.2Gi       331Mi       6.9Gi        13Gi
Swap:          4.0Gi       4.0Ki       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15875 MB
node 0 free: 7348 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop     4K    0 squashfs /snap/bare/5                        
loop1       loop    74M    0 squashfs /snap/core22/2955                   
loop2       loop  66.8M    0 squashfs /snap/core24/1587                   
loop3       loop    74M    0 squashfs /snap/core22/2411                   
loop4       loop  66.8M    0 squashfs /snap/core24/2124                   
loop5       loop 262.2M    0 squashfs /snap/firefox/8995                  
loop6       loop 261.4M    0 squashfs /snap/firefox/8969                  
loop7       loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop8       loop  12.7M    0 squashfs /snap/firmware-updater/258          
loop9       loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop10      loop 531.5M    0 squashfs /snap/gnome-42-2204/263             
loop11      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop12      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop13      loop  11.8M    0 squashfs /snap/snap-store/1427               
loop14      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop15      loop   402M    0 squashfs /snap/mesa-2404/1839                
loop16      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop17      loop  50.3M    0 squashfs /snap/snapd/27738                   
loop18      loop  44.7M    0 squashfs /snap/snapd/28254                   
loop19      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
loop20      loop   828K    0 squashfs /snap/snapd-desktop-integration/391 
loop21      loop 615.3M    0 squashfs /snap/gnome-46-2404/168             
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLW256HEHP-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   34G  188G  16% /
efivarfs       efivarfs  256K   61K  191K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### GPU

```text
index, name, memory.total [MiB], driver_version, compute_cap, pci.bus_id
0, Quadro P600, 2048 MiB, 580.178.04, 6.1, 00000000:01:00.0
Display controllers (lspci):
00:02.0 Display controller: Intel Corporation HD Graphics 630 (rev 04)
01:00.0 VGA compatible controller: NVIDIA Corporation GP107GL [Quadro P600] (rev a1)
```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        UP             10.203.0.107/24 
wlp3s0           UP             192.168.4.107/22 fd47:fae1:3712:1:2357:f689:187a:1eac/64 fd47:fae1:3712:1:eb9e:9b0c:d816:afae/64 fd47:fae1:3712:1:23e2:80cf:4bbe:a103/64 fe80::b4cf:1583:b623:5b40/64 
default via 192.168.4.1 dev wlp3s0 proto dhcp src 192.168.4.107 metric 600 
```

#### Process and memory limits

```text
page_size=4096
open_files_soft=1024
open_files_hard=1048576
transparent_hugepages=always [madvise] never
```

### 192.168.4.108

- Hostname: `showgirl`
- CPU model: `Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz`
- Online CPUs: `8`
- Memory bytes: `16692604928`
- GPUs: `Quadro P600 (2.0 GiB)`
- NVIDIA driver: `580.178.04`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: `cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
- Uptime: `up 1 day, 23 hours, 7 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.5 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.5 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=noble
LOGO=ubuntu-logo
```

#### CPU topology

```text
Architecture:                            x86_64
CPU op-mode(s):                          32-bit, 64-bit
Address sizes:                           39 bits physical, 48 bits virtual
Byte Order:                              Little Endian
CPU(s):                                  8
On-line CPU(s) list:                     0-7
Vendor ID:                               GenuineIntel
Model name:                              Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz
CPU family:                              6
Model:                                   158
Thread(s) per core:                      2
Core(s) per socket:                      4
Socket(s):                               1
Stepping:                                9
CPU(s) scaling MHz:                      74%
CPU max MHz:                             3800.0000
CPU min MHz:                             800.0000
BogoMIPS:                                5799.77
Flags:                                   fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl smx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault epb pti ssbd ibrs ibpb stibp fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp md_clear flush_l1d arch_capabilities
L1d cache:                               128 KiB (4 instances)
L1i cache:                               128 KiB (4 instances)
L2 cache:                                1 MiB (4 instances)
L3 cache:                                8 MiB (1 instance)
NUMA node(s):                            1
NUMA node0 CPU(s):                       0-7
Vulnerability Gather data sampling:      Vulnerable
Vulnerability Ghostwrite:                Not affected
Vulnerability Indirect target selection: Not affected
Vulnerability Itlb multihit:             KVM: Mitigation: VMX unsupported
Vulnerability L1tf:                      Mitigation; PTE Inversion
Vulnerability Mds:                       Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Meltdown:                  Mitigation; PTI
Vulnerability Mmio stale data:           Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Old microcode:             Not affected
Vulnerability Reg file data sampling:    Not affected
Vulnerability Retbleed:                  Mitigation; IBRS
Vulnerability Spec rstack overflow:      Not affected
Vulnerability Spec store bypass:         Mitigation; Speculative Store Bypass disabled via prctl
Vulnerability Spectre v1:                Mitigation; usercopy/swapgs barriers and __user pointer sanitization
Vulnerability Spectre v2:                Mitigation; IBRS; IBPB conditional; STIBP conditional; RSB filling; PBRSB-eIBRS Not affected; BHI Not affected
Vulnerability Srbds:                     Mitigation; Microcode
Vulnerability Tsa:                       Not affected
Vulnerability Tsx async abort:           Mitigation; TSX disabled
Vulnerability Vmscape:                   Mitigation; IBPB before exit to userspace
```

#### Memory

```text
               total        used        free      shared  buff/cache   available
Mem:            15Gi       2.0Gi       3.7Gi       335Mi        10Gi        13Gi
Swap:          4.0Gi       304Ki       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15919 MB
node 0 free: 3781 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop    74M    0 squashfs /snap/core22/2411                   
loop1       loop     4K    0 squashfs /snap/bare/5                        
loop2       loop    74M    0 squashfs /snap/core22/2955                   
loop3       loop  66.8M    0 squashfs /snap/core24/1587                   
loop4       loop  66.8M    0 squashfs /snap/core24/2124                   
loop5       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop6       loop 262.2M    0 squashfs /snap/firefox/8995                  
loop7       loop  16.5M    0 squashfs /snap/firmware-updater/226          
loop8       loop  12.7M    0 squashfs /snap/firmware-updater/258          
loop9       loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop10      loop 531.5M    0 squashfs /snap/gnome-42-2204/263             
loop11      loop 615.3M    0 squashfs /snap/gnome-46-2404/168             
loop12      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop13      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop14      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop15      loop   402M    0 squashfs /snap/mesa-2404/1839                
loop16      loop  11.8M    0 squashfs /snap/snap-store/1427               
loop17      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop19      loop  50.3M    0 squashfs /snap/snapd/27738                   
loop20      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
loop21      loop   828K    0 squashfs /snap/snapd-desktop-integration/391 
loop22      loop  44.7M    0 squashfs /snap/snapd/28254                   
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLB256HAHQ-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   53G  169G  24% /
efivarfs       efivarfs  256K   62K  190K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### GPU

```text
index, name, memory.total [MiB], driver_version, compute_cap, pci.bus_id
0, Quadro P600, 2048 MiB, 580.178.04, 6.1, 00000000:01:00.0
Display controllers (lspci):
01:00.0 VGA compatible controller: NVIDIA Corporation GP107GL [Quadro P600] (rev a1)
```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        UP             10.203.0.108/24 
wlp3s0           UP             192.168.4.108/22 fd47:fae1:3712:1:b9c2:d085:ae22:7283/64 fd47:fae1:3712:1:4dab:4ce5:fddf:5486/64 fd47:fae1:3712:1:81ad:fab6:3763:5ef0/64 fe80::f181:83a3:7fab:6f4b/64 
default via 192.168.4.1 dev wlp3s0 proto dhcp src 192.168.4.108 metric 600 
```

#### Process and memory limits

```text
page_size=4096
open_files_soft=1024
open_files_hard=1048576
transparent_hugepages=always [madvise] never
```

### 192.168.4.151

- Hostname: `uther`
- CPU model: `11th Gen Intel(R) Core(TM) i7-11800H @ 2.30GHz`
- Online CPUs: `16`
- Memory bytes: `41741320192`
- GPUs: `NVIDIA GeForce RTX 3060 Laptop GPU (6.0 GiB)`
- NVIDIA driver: `580.178.04`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: `cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
- Uptime: `up 1 day, 22 hours, 18 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.5 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.5 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=noble
LOGO=ubuntu-logo
```

#### CPU topology

```text
Architecture:                            x86_64
CPU op-mode(s):                          32-bit, 64-bit
Address sizes:                           39 bits physical, 48 bits virtual
Byte Order:                              Little Endian
CPU(s):                                  16
On-line CPU(s) list:                     0-15
Vendor ID:                               GenuineIntel
Model name:                              11th Gen Intel(R) Core(TM) i7-11800H @ 2.30GHz
CPU family:                              6
Model:                                   141
Thread(s) per core:                      2
Core(s) per socket:                      8
Socket(s):                               1
Stepping:                                1
CPU(s) scaling MHz:                      60%
CPU max MHz:                             4600.0000
CPU min MHz:                             800.0000
BogoMIPS:                                4608.00
Flags:                                   fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf tsc_known_freq pni pclmulqdq dtes64 monitor ds_cpl vmx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault epb cat_l2 cdp_l2 ssbd ibrs ibpb stibp ibrs_enhanced tpr_shadow flexpriority ept vpid ept_ad fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid rdt_a avx512f avx512dq rdseed adx smap avx512ifma clflushopt clwb intel_pt avx512cd sha_ni avx512bw avx512vl xsaveopt xsavec xgetbv1 xsaves split_lock_detect user_shstk dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp hwp_pkg_req vnmi avx512vbmi umip pku ospke avx512_vbmi2 gfni vaes vpclmulqdq avx512_vnni avx512_bitalg avx512_vpopcntdq rdpid movdiri movdir64b fsrm avx512_vp2intersect md_clear ibt flush_l1d arch_capabilities
Virtualization:                          VT-x
L1d cache:                               384 KiB (8 instances)
L1i cache:                               256 KiB (8 instances)
L2 cache:                                10 MiB (8 instances)
L3 cache:                                24 MiB (1 instance)
NUMA node(s):                            1
NUMA node0 CPU(s):                       0-15
Vulnerability Gather data sampling:      Vulnerable
Vulnerability Ghostwrite:                Not affected
Vulnerability Indirect target selection: Mitigation; Aligned branch/return thunks
Vulnerability Itlb multihit:             Not affected
Vulnerability L1tf:                      Not affected
Vulnerability Mds:                       Not affected
Vulnerability Meltdown:                  Not affected
Vulnerability Mmio stale data:           Not affected
Vulnerability Old microcode:             Not affected
Vulnerability Reg file data sampling:    Not affected
Vulnerability Retbleed:                  Not affected
Vulnerability Spec rstack overflow:      Not affected
Vulnerability Spec store bypass:         Mitigation; Speculative Store Bypass disabled via prctl
Vulnerability Spectre v1:                Mitigation; usercopy/swapgs barriers and __user pointer sanitization
Vulnerability Spectre v2:                Mitigation; Enhanced / Automatic IBRS; IBPB conditional; PBRSB-eIBRS SW sequence; BHI SW loop, KVM SW loop
Vulnerability Srbds:                     Not affected
Vulnerability Tsa:                       Not affected
Vulnerability Tsx async abort:           Not affected
Vulnerability Vmscape:                   Not affected
```

#### Memory

```text
               total        used        free      shared  buff/cache   available
Mem:            38Gi       7.2Gi       8.2Gi       658Mi        24Gi        31Gi
Swap:          4.0Gi       1.5Gi       2.5Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15
node 0 size: 39807 MB
node 0 free: 8440 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop     4K    0 squashfs /snap/bare/5                        
loop1       loop    74M    0 squashfs /snap/core22/2437                   
loop2       loop    74M    0 squashfs /snap/core22/2955                   
loop3       loop  66.8M    0 squashfs /snap/core24/1643                   
loop4       loop 261.3M    0 squashfs /snap/firefox/8929                  
loop5       loop  66.8M    0 squashfs /snap/core24/2124                   
loop6       loop 262.2M    0 squashfs /snap/firefox/8995                  
loop7       loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop8       loop  16.5M    0 squashfs /snap/firmware-updater/226          
loop9       loop 242.6M    0 squashfs /snap/gaming-graphics-core24/13     
loop10      loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop11      loop 531.5M    0 squashfs /snap/gnome-42-2204/263             
loop12      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop13      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop14      loop   402M    0 squashfs /snap/mesa-2404/1839                
loop15      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop16      loop  11.8M    0 squashfs /snap/snap-store/1390               
loop17      loop  11.8M    0 squashfs /snap/snap-store/1419               
loop18      loop  50.3M    0 squashfs /snap/snapd/27738                   
loop19      loop  44.7M    0 squashfs /snap/snapd/28254                   
loop20      loop   828K    0 squashfs /snap/snapd-desktop-integration/391 
loop21      loop   828K    0 squashfs /snap/snapd-desktop-integration/387 
loop22      loop 291.2M    0 squashfs /snap/steam/271                     
nvme0n1     disk 476.9G    0                                              PM981 NVMe Samsung 512GB
├─nvme0n1p1 part 469.4G    0 ext4     /                                   
└─nvme0n1p2 part     1G    0 vfat     /boot/efi                           
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p1 ext4      461G  232G  207G  53% /
efivarfs       efivarfs  192K  119K   69K  64% /sys/firmware/efi/efivars
/dev/nvme0n1p2 vfat      1.1G   24M  1.1G   3% /boot/efi
```

#### Watchdog

```text

```

#### GPU

```text
index, name, memory.total [MiB], driver_version, compute_cap, pci.bus_id
0, NVIDIA GeForce RTX 3060 Laptop GPU, 6144 MiB, 580.178.04, 8.6, 00000000:01:00.0
Display controllers (lspci):
00:02.0 VGA compatible controller: Intel Corporation TigerLake-H GT1 [UHD Graphics] (rev 01)
01:00.0 VGA compatible controller: NVIDIA Corporation GA106M [GeForce RTX 3060 Mobile / Max-Q] (rev a1)
```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp46s0          UP             10.203.0.151/24 
wlp48s0          UP             192.168.4.151/22 fd47:fae1:3712:1:82c9:2ecb:eef8:fbdb/64 fd47:fae1:3712:1:89cf:2ec6:f0a4:221d/64 fd47:fae1:3712:1:9006:b3a0:af83:3ecb/64 fe80::f5db:bfa5:3886:9b89/64 
default via 192.168.4.1 dev wlp48s0 proto dhcp src 192.168.4.151 metric 600 
```

#### Process and memory limits

```text
page_size=4096
open_files_soft=1024
open_files_hard=1048576
transparent_hugepages=always [madvise] never
```

### 192.168.4.156

- Hostname: `gawain`
- CPU model: `Intel(R) Core(TM) i7-9850H CPU @ 2.60GHz`
- Online CPUs: `12`
- Memory bytes: `7909330944`
- GPUs: `Quadro T1000 (4.0 GiB)`
- NVIDIA driver: `580.178.04`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: `cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
- Uptime: `up 2 days, 4 hours, 37 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.5 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.5 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=noble
LOGO=ubuntu-logo
```

#### CPU topology

```text
Architecture:                            x86_64
CPU op-mode(s):                          32-bit, 64-bit
Address sizes:                           39 bits physical, 48 bits virtual
Byte Order:                              Little Endian
CPU(s):                                  12
On-line CPU(s) list:                     0-11
Vendor ID:                               GenuineIntel
Model name:                              Intel(R) Core(TM) i7-9850H CPU @ 2.60GHz
CPU family:                              6
Model:                                   158
Thread(s) per core:                      2
Core(s) per socket:                      6
Socket(s):                               1
Stepping:                                13
CPU(s) scaling MHz:                      63%
CPU max MHz:                             4600.0000
CPU min MHz:                             800.0000
BogoMIPS:                                5199.98
Flags:                                   fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl vmx smx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault epb ssbd ibrs ibpb stibp ibrs_enhanced tpr_shadow flexpriority ept vpid ept_ad fsgsbase tsc_adjust sgx bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp vnmi sgx_lc md_clear flush_l1d arch_capabilities
Virtualization:                          VT-x
L1d cache:                               192 KiB (6 instances)
L1i cache:                               192 KiB (6 instances)
L2 cache:                                1.5 MiB (6 instances)
L3 cache:                                12 MiB (1 instance)
NUMA node(s):                            1
NUMA node0 CPU(s):                       0-11
Vulnerability Gather data sampling:      Vulnerable
Vulnerability Ghostwrite:                Not affected
Vulnerability Indirect target selection: Mitigation; Aligned branch/return thunks
Vulnerability Itlb multihit:             KVM: Mitigation: Split huge pages
Vulnerability L1tf:                      Not affected
Vulnerability Mds:                       Not affected
Vulnerability Meltdown:                  Not affected
Vulnerability Mmio stale data:           Mitigation; Clear CPU buffers; SMT vulnerable
Vulnerability Old microcode:             Not affected
Vulnerability Reg file data sampling:    Not affected
Vulnerability Retbleed:                  Mitigation; Enhanced IBRS
Vulnerability Spec rstack overflow:      Not affected
Vulnerability Spec store bypass:         Mitigation; Speculative Store Bypass disabled via prctl
Vulnerability Spectre v1:                Mitigation; usercopy/swapgs barriers and __user pointer sanitization
Vulnerability Spectre v2:                Mitigation; Enhanced / Automatic IBRS; IBPB conditional; PBRSB-eIBRS SW sequence; BHI SW loop, KVM SW loop
Vulnerability Srbds:                     Mitigation; Microcode
Vulnerability Tsa:                       Not affected
Vulnerability Tsx async abort:           Mitigation; TSX disabled
Vulnerability Vmscape:                   Mitigation; IBPB before exit to userspace
```

#### Memory

```text
               total        used        free      shared  buff/cache   available
Mem:           7.4Gi       2.7Gi       974Mi       701Mi       4.7Gi       4.7Gi
Swap:          8.0Gi       2.9Mi       8.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7 8 9 10 11
node 0 size: 7542 MB
node 0 free: 974 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop  44.7M    0 squashfs /snap/snapd/28254                   
loop1       loop     4K    0 squashfs /snap/bare/5                        
loop2       loop 273.5M    0 squashfs /snap/firefox/8054                  
loop3       loop    74M    0 squashfs /snap/core22/2411                   
loop4       loop  66.8M    0 squashfs /snap/core24/1643                   
loop5       loop  66.8M    0 squashfs /snap/core24/2124                   
loop6       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop7       loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop8       loop  16.5M    0 squashfs /snap/firmware-updater/226          
loop9       loop   402M    0 squashfs /snap/mesa-2404/1839                
loop10      loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop11      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop12      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop13      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop14      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop15      loop  15.7M    0 squashfs /snap/snap-store/1367               
loop16      loop   828K    0 squashfs /snap/snapd-desktop-integration/391 
loop17      loop 531.5M    0 squashfs /snap/gnome-42-2204/263             
loop18      loop  49.3M    0 squashfs /snap/snapd/26865                   
loop19      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
loop20      loop    74M    0 squashfs /snap/core22/2955                   
nvme0n1     disk 953.9G    0                                              KINGSTON OM8PCP31024F-AI1
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 952.8G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      937G   31G  859G   4% /
efivarfs       efivarfs  246K   64K  178K  27% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### GPU

```text
index, name, memory.total [MiB], driver_version, compute_cap, pci.bus_id
0, Quadro T1000, 4096 MiB, 580.178.04, 7.5, 00000000:01:00.0
Display controllers (lspci):
00:02.0 VGA compatible controller: Intel Corporation CoffeeLake-H GT2 [UHD Graphics 630] (rev 02)
01:00.0 VGA compatible controller: NVIDIA Corporation TU117GLM [Quadro T1000 Mobile] (rev a1)
```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        DOWN           
wlp82s0          UP             192.168.4.156/22 fd47:fae1:3712:1:109f:caed:9022:9813/64 fd47:fae1:3712:1:e784:7068:aa38:8951/64 fd47:fae1:3712:1:febd:dd4a:c62f:4777/64 fe80::eb25:9a0a:ebf5:36c0/64 
enx9c69d3934244  UP             10.203.0.156/24 
default via 192.168.4.1 dev wlp82s0 proto dhcp src 192.168.4.156 metric 600 
```

#### Process and memory limits

```text
page_size=4096
open_files_soft=1024
open_files_hard=1048576
transparent_hugepages=always [madvise] never
```

