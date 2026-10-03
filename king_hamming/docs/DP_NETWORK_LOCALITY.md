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

## Status

| Step | State |
| --- | --- |
| 1. Local replica first, shared bounded cache | done (`cluster/dependency_cache.py`) |
| 2. Border-only input format | done: edge bands (below) |
| 3. Soft row affinity | done: a bounded tie-break in the lease choice (below) |

Measured on a real interior `13^9` tile (90,25), whose three predecessors held
65.1 MiB of packets: the three bands it needs are **1.47 MiB (44x less)**, and the
assembled halo is byte-identical to the one built from whole packets. Remaining:
compare network bytes per completed tile on the live campaign (`progress_details`
records them, see "Measuring").

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
   **As built.** When a tile finishes, its worker cuts three bands from the tile's
   values and indexes them with the leader, by the hash of the tile's packet:
   `bottom` (the last `p^2` rows, full width), `right` (the last `p^2` columns, full
   height) and `corner` (their overlap). A successor needs, from each predecessor,
   the `bottom` band if it is in the same tile column, the `right` band if it is in
   the same tile row, and the `corner` otherwise (`tiles.band_kind`). A band is a
   deterministic gzip blob (`dp_solver/bands.py`): one JSON identity line (format,
   field, source tile, kind, global cell range, byte order) then the raw values.
   Bands are ordinary content-addressed artifacts, so the existing replication,
   hard-linking and dependency cache apply, and a band is about 1 MiB or less
   against a 25 MiB packet. Tiles with no successor on a side do not publish that
   band. The packet is unchanged and still serves reconstruction, durability and
   `reuse_tiles`.

   **Compatibility and safety.** The leader lists a band in a predecessor's
   descriptor only while it has a live replica, and always lists the packet as
   well. A worker that finds any band unusable (missing, unreachable, hash or
   identity mismatch, wrong length) logs it and fetches the packet instead, so old
   tiles, imported tiles and half-replicated bands all still work. A worker that
   cannot publish bands (old leader, disk or network error) still completes the
   tile. `build_halo` refuses a piece that does not cover every halo cell it must
   fill. `KH_DP_BANDS=0` in a worker's environment turns publishing and use off.

3. **Soft row affinity.** Prefer a node holding the left predecessor and other
   required replicas, but let any eligible node take the tile if that node is
   busy or unhealthy. Do not lease an entire row exclusively: each tile also
   depends on the row above, and hard row ownership would reduce the ready
   wave's parallelism. Once the shared cache and bands exist, measure whether
   row affinity still improves throughput enough to justify scheduler cost.

   **As built.** The leader asks the adapter to score the queued candidates for the
   node that is leasing (`locality_scores`; DP: `distributed.locality_scores`): 3 when
   the node ran the tile's left neighbour, 2 when it merely stores it, plus 1 when
   it stores the upper neighbour. Candidates are then sorted by the unchanged queue
   order (reconstruction, priority, the root's running tiles, estimate) with the
   score inserted just before creation time, so affinity can only separate tiles of
   the same root and priority. A tile that has waited longer than 15 minutes
   (`KH_ROW_AFFINITY_WAIT` seconds) scores above everything, so preference delays no
   tile by more than that. No node is ever denied work, and rows are not leased.
   `KH_ROW_AFFINITY=0` in the leader's environment restores the plain order.

   **Expected value.** With bands, the left neighbour's `right` band is about a
   third of a tile's already small input (0.46 of 1.47 MiB in the example above),
   so affinity now saves well under 1% of the original traffic. It is cheap, and
   it helps most when each tile has few copies (CAMPAIGN_NOTES item 23), but the
   measurement below decides whether it earns its keep.

## Measuring

Every tile's final progress record carries `input_mode` (`bands`, `packets`,
`mixed` or `none`), `input_bytes`, `input_band_bytes` and `bands_published`,
stored in `runs.progress_details`. Compare `AVG(input_bytes)` of tiles run before
and after the upgrade, and the fraction run on a node that produced their left
neighbour, for example:

```sql
SELECT json_extract(progress_details,'$.input_mode') AS mode,
       COUNT(*), AVG(json_extract(progress_details,'$.input_bytes'))/1048576.0 AS mib
FROM runs WHERE json_extract(specification,'$.program')='dp_tile' AND state='complete'
GROUP BY mode;
```

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
