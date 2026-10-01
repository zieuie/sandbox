# king_hamming cluster inventory

Generated: `2026-09-27T01:17:37-05:00`

Command: `inventory_cluster.sh --run`

This report contains hardware and operating-system facts needed to size
resident fields, DP tiles, matching state, checkpoints, and watchdog setup.

Naming note: the physical leader is now called **merlin**, but its transplanted
SSD still reports the hostname `uther`. Both names refer to `192.168.4.151` in
the design until the hostname is changed.

Added 2026-10-01: `192.168.4.152` (**pellinore**, fleet name `dp-152`), an
Intel i7-8750H with 12 logical CPUs and 14.8 GiB RAM. Its section was collected
with `inventory_cluster.sh --run 192.168.4.152` and appended; the other
machines' data is still from the generation date above.

## Initial design implications

- All nine machines responded successfully over SSH.
- `.101` through `.108` form a nearly uniform pool: each has an Intel i7-7700T,
  8 logical CPUs, about 15.5 GiB RAM, one NUMA node, and a roughly 238 GiB NVMe
  system disk with about 204-206 GiB free.
- `.151` (merlin, currently reporting `uther`) is the larger coordinator/heavy-worker candidate: an Intel
  i7-11800H, 16 logical CPUs, about 38.9 GiB RAM, one NUMA node, and about
  297 GiB free on its NVMe system disk.
- No `/dev/watchdog*` devices were visible and no kernel watchdog identities
  were reported. Automatic recovery cannot assume a configured hardware
  watchdog; firmware/kernel support and external power control need a separate
  investigation.
- The eight `.101`-`.108` nodes did not report a `cc` executable. Production
  binaries should initially be built centrally and deployed as artifacts, or a
  compiler toolchain must be installed consistently on those nodes.
- All nodes report Ubuntu 24.04 and systemd. The uniform eight-node pool is a
  good fit for identical worker services and memory limits; `.151` can accept
  larger resident fields or coordinate/checkpoint work.

## Summary

| Address | Status | Hostname | Architecture | CPUs | Memory | NUMA | Watchdog | OS/kernel |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |
| 192.168.4.101 | ok | fearless | x86_64 | 8 | 15.5 GiB | 1 | none | Linux 6.17.0-20-generic |
| 192.168.4.102 | ok | red | x86_64 | 8 | 15.5 GiB | 1 | none | Linux 6.17.0-20-generic |
| 192.168.4.103 | ok | lover | x86_64 | 8 | 15.5 GiB | 1 | none | Linux 6.17.0-20-generic |
| 192.168.4.104 | ok | folklore | x86_64 | 8 | 15.5 GiB | 1 | none | Linux 6.17.0-20-generic |
| 192.168.4.105 | ok | evermore | x86_64 | 8 | 15.5 GiB | 1 | none | Linux 6.17.0-20-generic |
| 192.168.4.106 | ok | midnights | x86_64 | 8 | 15.5 GiB | 1 | none | Linux 6.17.0-20-generic |
| 192.168.4.107 | ok | poets | x86_64 | 8 | 15.5 GiB | 1 | none | Linux 6.17.0-20-generic |
| 192.168.4.108 | ok | showgirl | x86_64 | 8 | 15.5 GiB | 1 | none | Linux 6.17.0-20-generic |
| 192.168.4.151 | ok | uther | x86_64 | 16 | 38.9 GiB | 1 | none | Linux 7.0.0-31-generic |
| 192.168.4.152 | ok | pellinore | x86_64 | 12 | 14.8 GiB | 1 | none | Linux 7.0.0-38-generic |

## Machine details

### 192.168.4.101

- Hostname: `fearless`
- CPU model: `Intel(R) Core(TM) i7-7700T CPU @ 2.90GHz`
- Online CPUs: `8`
- Memory bytes: `16650059776`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: ``
- Uptime: `up 3 hours, 56 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.4 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.4 LTS (Noble Numbat)"
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
Mem:            15Gi       1.1Gi        12Gi        10Mi       2.2Gi        14Gi
Swap:          4.0Gi          0B       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15878 MB
node 0 free: 12797 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop    74M    0 squashfs /snap/core22/2339                   
loop1       loop    74M    0 squashfs /snap/core22/2411                   
loop2       loop  66.8M    0 squashfs /snap/core24/1587                   
loop3       loop     4K    0 squashfs /snap/bare/5                        
loop5       loop  66.8M    0 squashfs /snap/core24/1499                   
loop6       loop  16.4M    0 squashfs /snap/firmware-updater/223          
loop7       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop8       loop 505.1M    0 squashfs /snap/gnome-42-2204/176             
loop9       loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop10      loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop11      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop12      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop13      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop14      loop 261.4M    0 squashfs /snap/firefox/8969                  
loop15      loop  48.1M    0 squashfs /snap/snapd/25935                   
loop16      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop17      loop  48.4M    0 squashfs /snap/snapd/26382                   
loop18      loop   580K    0 squashfs /snap/snapd-desktop-integration/357 
loop19      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
loop20      loop  11.8M    0 squashfs /snap/snap-store/1427               
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLW256HEHP-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   15G  206G   7% /
efivarfs       efivarfs  256K   61K  191K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        DOWN           
wlp3s0           UP             192.168.4.101/22 fd47:fae1:3712:1:e7de:bb22:5e70:b790/64 fd47:fae1:3712:1:e7c7:e08a:4c99:515c/64 fe80::667b:4a36:44f9:87ac/64 
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
- Memory bytes: `16650436608`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: ``
- Uptime: `up 3 hours, 56 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.4 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.4 LTS (Noble Numbat)"
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
Mem:            15Gi       1.0Gi        13Gi         9Mi       1.1Gi        14Gi
Swap:          4.0Gi          0B       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15879 MB
node 0 free: 14004 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop    74M    0 squashfs /snap/core22/2339                   
loop1       loop  66.8M    0 squashfs /snap/core24/1499                   
loop2       loop  66.8M    0 squashfs /snap/core24/1587                   
loop3       loop     4K    0 squashfs /snap/bare/5                        
loop4       loop 273.5M    0 squashfs /snap/firefox/8054                  
loop5       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop6       loop  16.4M    0 squashfs /snap/firmware-updater/223          
loop7       loop    74M    0 squashfs /snap/core22/2411                   
loop8       loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop9       loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop10      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop11      loop 505.1M    0 squashfs /snap/gnome-42-2204/176             
loop12      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop13      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop14      loop  15.5M    0 squashfs /snap/snap-store/1310               
loop15      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop16      loop  48.4M    0 squashfs /snap/snapd/26382                   
loop17      loop  49.3M    0 squashfs /snap/snapd/26865                   
loop18      loop   580K    0 squashfs /snap/snapd-desktop-integration/357 
loop19      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLB256HAHQ-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   16G  206G   7% /
efivarfs       efivarfs  256K   62K  190K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        DOWN           
wlp3s0           UP             192.168.4.102/22 fd47:fae1:3712:1:329d:bc7e:1bd4:18a3/64 fd47:fae1:3712:1:3000:6102:9129:16bd/64 fe80::216a:519d:7903:5a16/64 
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
- Memory bytes: `16650436608`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: ``
- Uptime: `up 3 hours, 56 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.4 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.4 LTS (Noble Numbat)"
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
CPU(s) scaling MHz:                      96%
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
Mem:            15Gi       1.1Gi        11Gi         9Mi       3.0Gi        14Gi
Swap:          4.0Gi          0B       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15879 MB
node 0 free: 12052 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop     4K    0 squashfs /snap/bare/5                        
loop1       loop    74M    0 squashfs /snap/core22/2411                   
loop2       loop    74M    0 squashfs /snap/core22/2292                   
loop3       loop  66.8M    0 squashfs /snap/core24/1499                   
loop4       loop  66.8M    0 squashfs /snap/core24/1587                   
loop5       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop6       loop 273.5M    0 squashfs /snap/firefox/8054                  
loop7       loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop8       loop  16.5M    0 squashfs /snap/firmware-updater/226          
loop9       loop 505.1M    0 squashfs /snap/gnome-42-2204/176             
loop10      loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop11      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop12      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop13      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop14      loop  15.5M    0 squashfs /snap/snap-store/1310               
loop15      loop  48.4M    0 squashfs /snap/snapd/26382                   
loop16      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop17      loop  48.1M    0 squashfs /snap/snapd/25935                   
loop18      loop   576K    0 squashfs /snap/snapd-desktop-integration/343 
loop19      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLB256HAHQ-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   16G  206G   7% /
efivarfs       efivarfs  256K   61K  191K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        DOWN           
wlp3s0           UP             192.168.4.103/22 fd47:fae1:3712:1:5ee8:acc8:6b79:f5aa/64 fd47:fae1:3712:1:3f59:e9e0:b454:b2c0/64 fe80::3fd9:5d5f:c644:dfb6/64 
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
- Memory bytes: `16650051584`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: ``
- Uptime: `up 3 hours, 56 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.4 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.4 LTS (Noble Numbat)"
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
CPU(s) scaling MHz:                      96%
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
Mem:            15Gi       1.0Gi        13Gi       9.9Mi       1.1Gi        14Gi
Swap:          4.0Gi          0B       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15878 MB
node 0 free: 13988 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop     4K    0 squashfs /snap/bare/5                        
loop1       loop    74M    0 squashfs /snap/core22/2339                   
loop2       loop    74M    0 squashfs /snap/core22/2411                   
loop3       loop  66.8M    0 squashfs /snap/core24/1499                   
loop4       loop  66.8M    0 squashfs /snap/core24/1587                   
loop5       loop 273.5M    0 squashfs /snap/firefox/8054                  
loop6       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop7       loop  16.4M    0 squashfs /snap/firmware-updater/223          
loop8       loop 505.1M    0 squashfs /snap/gnome-42-2204/176             
loop9       loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop10      loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop11      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop12      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop13      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop14      loop  15.5M    0 squashfs /snap/snap-store/1310               
loop15      loop  48.1M    0 squashfs /snap/snapd/25935                   
loop16      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop17      loop  48.4M    0 squashfs /snap/snapd/26382                   
loop18      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
loop19      loop   580K    0 squashfs /snap/snapd-desktop-integration/357 
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLW256HEHP-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   16G  206G   7% /
efivarfs       efivarfs  256K   61K  191K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        DOWN           
wlp3s0           UP             192.168.4.104/22 fd47:fae1:3712:1:acaf:ad60:a6ab:ff5f/64 fd47:fae1:3712:1:35ce:cc46:c60b:54aa/64 fe80::a7f4:38ca:7476:60f/64 
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
- Memory bytes: `16650432512`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: ``
- Uptime: `up 4 hours, 4 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.4 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.4 LTS (Noble Numbat)"
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
Mem:            15Gi       1.1Gi        11Gi       9.9Mi       2.9Gi        14Gi
Swap:          4.0Gi          0B       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15879 MB
node 0 free: 12074 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop     4K    0 squashfs /snap/bare/5                        
loop1       loop    74M    0 squashfs /snap/core22/2339                   
loop2       loop    74M    0 squashfs /snap/core22/2411                   
loop3       loop  66.8M    0 squashfs /snap/core24/1499                   
loop4       loop  66.8M    0 squashfs /snap/core24/1587                   
loop5       loop 273.5M    0 squashfs /snap/firefox/8054                  
loop6       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop7       loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop8       loop  16.4M    0 squashfs /snap/firmware-updater/216          
loop9       loop 516.2M    0 squashfs /snap/gnome-42-2204/226             
loop10      loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop11      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop12      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop13      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop14      loop  15.5M    0 squashfs /snap/snap-store/1310               
loop15      loop  48.1M    0 squashfs /snap/snapd/25935                   
loop16      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop17      loop  48.4M    0 squashfs /snap/snapd/26382                   
loop18      loop   580K    0 squashfs /snap/snapd-desktop-integration/357 
loop19      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
nvme0n1     disk 238.5G    0                                              Micron MTFDHBA256TDV
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   18G  204G   8% /
efivarfs       efivarfs  256K   62K  190K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        DOWN           
wlp3s0           UP             192.168.4.105/22 fd47:fae1:3712:1:158d:fc21:156f:76da/64 fd47:fae1:3712:1:95cd:8836:f2b0:2506/64 fe80::3a51:80ff:7411:ec5d/64 
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
- Memory bytes: `16653574144`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: ``
- Uptime: `up 4 hours, 4 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.4 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.4 LTS (Noble Numbat)"
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
Mem:            15Gi       1.1Gi        12Gi       9.9Mi       2.0Gi        14Gi
Swap:          4.0Gi          0B       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15882 MB
node 0 free: 13013 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop     4K    0 squashfs /snap/bare/5                        
loop1       loop    74M    0 squashfs /snap/core22/2411                   
loop2       loop    74M    0 squashfs /snap/core22/2339                   
loop3       loop  66.8M    0 squashfs /snap/core24/1587                   
loop4       loop  66.8M    0 squashfs /snap/core24/1499                   
loop5       loop 273.5M    0 squashfs /snap/firefox/8054                  
loop6       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop7       loop  16.4M    0 squashfs /snap/firmware-updater/223          
loop8       loop 505.1M    0 squashfs /snap/gnome-42-2204/176             
loop9       loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop10      loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop11      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop12      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop13      loop  15.5M    0 squashfs /snap/snap-store/1310               
loop14      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop15      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop16      loop  48.4M    0 squashfs /snap/snapd/26382                   
loop17      loop  48.1M    0 squashfs /snap/snapd/25935                   
loop18      loop   576K    0 squashfs /snap/snapd-desktop-integration/343 
loop19      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLB256HAHQ-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   16G  206G   7% /
efivarfs       efivarfs  256K   59K  193K  24% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        DOWN           
wlp3s0           UP             192.168.4.106/22 fd47:fae1:3712:1:c1e5:54e7:208:187/64 fd47:fae1:3712:1:e699:5851:e159:9c76/64 fe80::fd32:40f4:b584:9cd8/64 
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
- Memory bytes: `16649965568`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: ``
- Uptime: `up 4 hours, 5 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.4 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.4 LTS (Noble Numbat)"
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
Mem:            15Gi       1.1Gi        13Gi       9.9Mi       1.7Gi        14Gi
Swap:          4.0Gi          0B       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15878 MB
node 0 free: 13353 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop     4K    0 squashfs /snap/bare/5                        
loop1       loop    74M    0 squashfs /snap/core22/2339                   
loop2       loop  66.8M    0 squashfs /snap/core24/1499                   
loop3       loop    74M    0 squashfs /snap/core22/2411                   
loop4       loop  66.8M    0 squashfs /snap/core24/1587                   
loop5       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop6       loop 273.5M    0 squashfs /snap/firefox/8054                  
loop7       loop 505.1M    0 squashfs /snap/gnome-42-2204/176             
loop8       loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop9       loop  16.4M    0 squashfs /snap/firmware-updater/223          
loop10      loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop11      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop12      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop13      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop14      loop  15.5M    0 squashfs /snap/snap-store/1310               
loop15      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop16      loop  48.1M    0 squashfs /snap/snapd/25935                   
loop17      loop  48.4M    0 squashfs /snap/snapd/26382                   
loop18      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
loop19      loop   580K    0 squashfs /snap/snapd-desktop-integration/357 
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLW256HEHP-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   16G  206G   8% /
efivarfs       efivarfs  256K   61K  191K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        DOWN           
wlp3s0           UP             192.168.4.107/22 fd47:fae1:3712:1:d0bc:3551:b459:8307/64 fd47:fae1:3712:1:23e2:80cf:4bbe:a103/64 fe80::b4cf:1583:b623:5b40/64 
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
- Memory bytes: `16696004608`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: ``
- Uptime: `up 4 hours, 5 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 24.04.4 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.4 LTS (Noble Numbat)"
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
Mem:            15Gi       1.0Gi        11Gi       4.8Mi       3.8Gi        14Gi
Swap:          4.0Gi          0B       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7
node 0 size: 15922 MB
node 0 free: 11279 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop     4K    0 squashfs /snap/bare/5                        
loop1       loop 615.3M    0 squashfs /snap/gnome-46-2404/168             
loop2       loop    74M    0 squashfs /snap/core22/2411                   
loop3       loop  66.8M    0 squashfs /snap/core24/1499                   
loop4       loop  66.8M    0 squashfs /snap/core24/1587                   
loop5       loop 273.5M    0 squashfs /snap/firefox/8054                  
loop6       loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop7       loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop8       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop9       loop 505.1M    0 squashfs /snap/gnome-42-2204/176             
loop10      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop11      loop  16.5M    0 squashfs /snap/firmware-updater/226          
loop12      loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop13      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop14      loop  48.1M    0 squashfs /snap/snapd/25935                   
loop16      loop  15.6M    0 squashfs /snap/snap-store/1338               
loop17      loop  48.4M    0 squashfs /snap/snapd/26382                   
loop18      loop   576K    0 squashfs /snap/snapd-desktop-integration/343 
loop19      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
loop20      loop  11.8M    0 squashfs /snap/snap-store/1427               
loop21      loop    74M    0 squashfs /snap/core22/2955                   
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLB256HAHQ-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   17G  205G   8% /
efivarfs       efivarfs  256K   62K  190K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.2M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp0s31f6        DOWN           
wlp3s0           UP             192.168.4.108/22 fd47:fae1:3712:1:b7d6:b56f:e82a:1110/64 fd47:fae1:3712:1:81ad:fab6:3763:5ef0/64 fe80::f181:83a3:7fab:6f4b/64 
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
- Memory bytes: `41741361152`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: `cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
- Uptime: `up 2 days, 16 hours, 7 minutes`

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
CPU(s) scaling MHz:                      45%
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
Mem:            38Gi        11Gi       5.3Gi       1.5Gi        24Gi        27Gi
Swap:          4.0Gi       316Ki       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15
node 0 size: 39807 MB
node 0 free: 5471 MB
node distances:
node   0 
  0:  10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop    74M    0 squashfs /snap/core22/2437                   
loop1       loop    74M    0 squashfs /snap/core22/2955                   
loop2       loop 260.8M    0 squashfs /snap/firefox/8863                  
loop3       loop     4K    0 squashfs /snap/bare/5                        
loop4       loop 261.3M    0 squashfs /snap/firefox/8929                  
loop5       loop  66.8M    0 squashfs /snap/core24/1643                   
loop6       loop  16.5M    0 squashfs /snap/firmware-updater/226          
loop7       loop  66.8M    0 squashfs /snap/core24/2124                   
loop8       loop 531.4M    0 squashfs /snap/gnome-42-2204/247             
loop9       loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop10      loop 242.6M    0 squashfs /snap/gaming-graphics-core24/13     
loop11      loop  16.4M    0 squashfs /snap/firmware-updater/224          
loop12      loop 531.5M    0 squashfs /snap/gnome-42-2204/263             
loop13      loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop14      loop   395M    0 squashfs /snap/mesa-2404/1165                
loop15      loop   402M    0 squashfs /snap/mesa-2404/1839                
loop16      loop  11.8M    0 squashfs /snap/snap-store/1419               
loop17      loop  50.1M    0 squashfs /snap/snapd/27710                   
loop18      loop  11.8M    0 squashfs /snap/snap-store/1390               
loop19      loop  50.3M    0 squashfs /snap/snapd/27738                   
loop20      loop 291.2M    0 squashfs /snap/steam/271                     
loop21      loop   828K    0 squashfs /snap/snapd-desktop-integration/391 
loop22      loop   828K    0 squashfs /snap/snapd-desktop-integration/387 
nvme0n1     disk 476.9G    0                                              PM981 NVMe Samsung 512GB
├─nvme0n1p1 part 469.4G    0 ext4     /                                   
└─nvme0n1p2 part     1G    0 vfat     /boot/efi                           
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p1 ext4      461G  142G  297G  33% /
efivarfs       efivarfs  192K  119K   69K  64% /sys/firmware/efi/efivars
/dev/nvme0n1p2 vfat      1.1G   24M  1.1G   3% /boot/efi
```

#### Watchdog

```text

```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
enp46s0          DOWN           
enx00e04c4640a0  DOWN           
wlp48s0          UP             192.168.4.151/22 fd47:fae1:3712:1:7a59:afbe:d66d:da19/64 fd47:fae1:3712:1:2f5f:6af1:74e6:127a/64 fd47:fae1:3712:1:9006:b3a0:af83:3ecb/64 fe80::f5db:bfa5:3886:9b89/64 
default via 192.168.4.1 dev wlp48s0 proto dhcp src 192.168.4.151 metric 600 
```

#### Process and memory limits

```text
page_size=4096
open_files_soft=1024
open_files_hard=1048576
transparent_hugepages=always [madvise] never
```

### 192.168.4.152

- Hostname: `pellinore`
- CPU model: `Intel(R) Core(TM) i7-8750H CPU @ 2.20GHz`
- Online CPUs: `12`
- Memory bytes: `15846043648`
- NUMA nodes: `1`
- Virtualization: `none`
- Watchdog devices: `none`
- systemd available: `yes`
- Compiler: ``
- Uptime: `up 1 hour, 2 minutes`

#### Operating system

```text
PRETTY_NAME="Ubuntu 26.04 LTS"
NAME="Ubuntu"
VERSION_ID="26.04"
VERSION="26.04 (Resolute Raccoon)"
VERSION_CODENAME=resolute
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=resolute
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
Model name:                              Intel(R) Core(TM) i7-8750H CPU @ 2.20GHz
CPU family:                              6
Model:                                   158
Thread(s) per core:                      2
Core(s) per socket:                      6
Socket(s):                               1
Stepping:                                10
CPU(s) scaling MHz:                      46%
CPU max MHz:                             4100.0000
CPU min MHz:                             800.0000
BogoMIPS:                                4399.99
Flags:                                   fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe syscall nx pdpe1gb rdtscp lm constant_tsc art arch_perfmon pebs bts rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni pclmulqdq dtes64 monitor ds_cpl vmx est tm2 ssse3 sdbg fma cx16 xtpr pdcm pcid sse4_1 sse4_2 x2apic movbe popcnt tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm 3dnowprefetch cpuid_fault epb pti ssbd ibrs ibpb stibp tpr_shadow flexpriority ept vpid ept_ad fsgsbase tsc_adjust sgx bmi1 avx2 smep bmi2 erms invpcid mpx rdseed adx smap clflushopt intel_pt xsaveopt xsavec xgetbv1 xsaves dtherm ida arat pln pts hwp hwp_notify hwp_act_window hwp_epp vnmi sgx_lc md_clear flush_l1d arch_capabilities
Virtualization:                          VT-x
L1d cache:                               192 KiB (6 instances)
L1i cache:                               192 KiB (6 instances)
L2 cache:                                1.5 MiB (6 instances)
L3 cache:                                9 MiB (1 instance)
NUMA node(s):                            1
NUMA node0 CPU(s):                       0-11
Vulnerability Gather data sampling:      Vulnerable
Vulnerability Ghostwrite:                Not affected
Vulnerability Indirect target selection: Not affected
Vulnerability Itlb multihit:             KVM: Mitigation: Split huge pages
Vulnerability L1tf:                      Mitigation; PTE Inversion; VMX conditional cache flushes, SMT vulnerable
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
Vulnerability Tsx async abort:           Not affected
Vulnerability Vmscape:                   Mitigation; IBPB before exit to userspace
```

#### Memory

```text
               total        used        free      shared  buff/cache   available
Mem:            14Gi       1.1Gi        12Gi        55Mi       2.0Gi        13Gi
Swap:          4.0Gi          0B       4.0Gi
```

#### NUMA

```text
available: 1 nodes (0)
node 0 cpus: 0 1 2 3 4 5 6 7 8 9 10 11
node 0 size: 15111 MB
node 0 free: 12344 MB
node distances:
node     0 
   0:   10 
```

#### Block storage

```text
NAME        TYPE   SIZE ROTA FSTYPE   MOUNTPOINTS                         MODEL
loop0       loop  66.8M    0 squashfs /snap/core24/1587                   
loop1       loop  19.6M    0 squashfs /snap/desktop-security-center/150   
loop2       loop     4K    0 squashfs /snap/bare/5                        
loop3       loop 273.7M    0 squashfs /snap/firefox/8107                  
loop4       loop 606.1M    0 squashfs /snap/gnome-46-2404/153             
loop5       loop  91.7M    0 squashfs /snap/gtk-common-themes/1535        
loop6       loop   395M    0 squashfs /snap/mesa-2404/1165                
loop7       loop  15.7M    0 squashfs /snap/snap-store/1367               
loop8       loop  18.8M    0 squashfs /snap/prompting-client/204          
loop9       loop  16.5M    0 squashfs /snap/firmware-updater/226          
loop10      loop   580K    0 squashfs /snap/snapd-desktop-integration/361 
loop11      loop  49.3M    0 squashfs /snap/snapd/26865                   
nvme0n1     disk 238.5G    0                                              SAMSUNG MZVLB256HAHQ-000L7
├─nvme0n1p1 part     1G    0 vfat     /boot/efi                           
└─nvme0n1p2 part 237.4G    0 ext4     /                                   
```

#### Mounted filesystems

```text
Filesystem     Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2 ext4      233G   11G  210G   5% /
efivarfs       efivarfs  384K   93K  287K  25% /sys/firmware/efi/efivars
/dev/nvme0n1p1 vfat      1.1G  6.4M  1.1G   1% /boot/efi
```

#### Watchdog

```text

```

#### Network

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128 
wlp59s0          UP             192.168.4.152/22 fd47:fae1:3712:1:4561:1983:1b38:29ba/64 fd47:fae1:3712:1:cf76:d4ed:b811:7719/64 fe80::dd00:a588:782:3245/64 
default via 192.168.4.1 dev wlp59s0 proto dhcp src 192.168.4.152 metric 600 
```

#### Process and memory limits

```text
page_size=4096
open_files_soft=1024
open_files_hard=524288
transparent_hugepages=always [madvise] never
```

