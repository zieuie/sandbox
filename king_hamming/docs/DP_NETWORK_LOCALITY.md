# Reducing distributed DP network traffic

## Measured problem (2026-10-02)

An interior 4096-square DP tile needs its top, left, and top-left predecessor
tiles. The current worker fetches a complete compressed tile packet for each
predecessor, unpacks the whole values and choices arrays, builds a thin input
halo, then deletes its private download directory. The last 100 `13^9` tile
packets had a median size of 23.69 MiB. Three such downloads are about 71 MiB
per tile, even though its `p²=169`-cell predecessor border contains only about
10.8 MiB of raw values. The choices in predecessor packets are not used by tile
evaluation. Completed packets also need durable replication, so download traffic
is not the only network cost.

## Incremental plan

1. **Local replica first, with a shared bounded cache.** If the executing node
   already stores a predecessor packet, hard-link that verified local blob into
   the lease's private input directory while holding its storage lock. Otherwise
   try the node's own blob URL before remote URLs. All tile processes on a node
   share a content-addressed download cache, so identical predecessors are
   downloaded once, not once per lease. Use the existing SHA-256 and exact-size
   checks; concurrent downloads remain serialized by the per-hash lock. Pin a
   checked-out blob with a private hard link before eviction. Evict least-recently
   used published cache entries and, if still over limit, inactive partial
   downloads under a small fixed byte limit; never evict an active transfer or
   lease-private link. Preserve ordinary peer fallback and the
   leader's replica index as the source of truth.
2. **Border-only input format.** Publish separately hashed bottom-row and
   right-column bands of tile values, each `p²` cells thick (clipped at edges).
   The bottom-right corner can be recovered from either band. Successors would
   fetch only the bands needed for their halo, then verify their hashes and
   coordinates before filling it. Keep the current complete tile packet and
   choice array for reconstruction and durability. Version the descriptor and
   support old packets until existing roots finish; never reinterpret a legacy
   packet as a band. Measure total bytes including sidecar replication before
   making the sidecars mandatory.
3. **Soft row affinity.** Prefer a node holding the left predecessor and other
   required replicas, but let any eligible node take the tile if that node is
   busy or unhealthy. Do not lease an entire row exclusively: each tile also
   depends on the row above, and hard row ownership would reduce the ready
   wave's parallelism. Once the shared cache and bands exist, measure whether
   row affinity still improves throughput enough to justify scheduler cost.

## Safety and success criteria

The cache is disposable, content-addressed, size-bounded, and outside durable
worker blob storage. Every cache hit is checked against the authorized
descriptor's exact size and SHA-256; a missing/corrupt local object falls back
to healthy peers. Garbage collection must not unlink a checked-out blob's
private hard link. A cache miss, node restart, or leader reassignment must not
change the mathematical output. Tests should cover concurrent same-hash fetches,
eviction during checkout, corruption, source fallback, and exact tile results.
Compare network bytes per completed tile, tile throughput, CPU occupancy,
failed/retried tiles, and disk free space against the retained campaign's
pre-change baseline. Keep the 2 GiB tile process limit until measurements show
a specific memory-bound case; raising it does not reduce network traffic.
