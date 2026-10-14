#!/bin/bash
# Climb a ladder of subset sizes: try m active orbits; on success, try m+step.
# Usage: ladder.sh n d m0 step try_secs total_secs [extra gpuclimb args]
n=$1 d=$2 m=$3 step=$4 try=$5 total=$6; shift 6
end=$(( $(date +%s) + total ))
while [ $(date +%s) -lt $end ]; do
  left=$(( end - $(date +%s) )); t=$(( try < left ? try : left ))
  echo "=== $(date '+%F %T') trying $m orbits for ${t}s"
  before=$(ls pa_${n}_${d}_gpu_[0-9]*.txt 2>/dev/null | wc -l)
  ./gpuclimb $n $d $t --active $m --ckpt 100000 "$@" | grep -E "group|subset|finished|verification|wrote|final"
  after=$(ls pa_${n}_${d}_gpu_[0-9]*.txt 2>/dev/null | wc -l)
  if [ $after -gt $before ]; then m=$(( m + step )); else echo "=== no solution at $m"; fi
done
