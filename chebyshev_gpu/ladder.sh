#!/bin/bash
# Climb a ladder of subset sizes: search for m active orbits; when that is
# solved, move on to m+step.  Resumable: the current m is kept in ladder.m and
# gpuclimb keeps every chain's best array in state_<n>_<d>_<rows>.bin, so
# killing this script (TERM/INT) and starting it again carries on where it was.
# Live progress: try.log (current try) and status.json (machine-readable).
# Usage: ladder.sh n d m0 step try_secs total_secs(0 = forever) [extra gpuclimb args]
n=$1 d=$2 m=$3 step=$4 try=$5 total=$6; shift 6
[ -f ladder.m ] && m=$(cat ladder.m)
stop=0; pid=
trap 'stop=1; [ -n "$pid" ] && kill -TERM $pid 2>/dev/null' TERM INT
start=$(date +%s)
while [ $stop -eq 0 ]; do
  if [ "$total" -gt 0 ]; then left=$(( start + total - $(date +%s) )); [ $left -le 0 ] && break; t=$(( try < left ? try : left )); else t=$try; fi
  echo "$m" > ladder.m
  echo "=== $(date '+%F %T') trying $m orbits for ${t}s"
  ./gpuclimb $n $d $t --active $m --ckpt 100000 "$@" > try.log 2>&1 &
  pid=$!
  while kill -0 $pid 2>/dev/null; do wait $pid; done
  pid=
  grep -E "group|subset|resuming|finished|verification|wrote|final|stopped" try.log
  if grep -q "VALID" try.log && ! grep -q "INVALID" try.log; then m=$(( m + step )); echo "$m" > ladder.m; fi
done
echo "=== $(date '+%F %T') ladder stopped at $m orbits"
