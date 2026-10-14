#!/bin/bash
# One line per search on the Chebyshev machines, from each
# ~/chebyshev_gpu/*/status.json (written every 30 s by gpuclimb).
HOSTS=${HOSTS:-"192.168.4.101 192.168.4.102 192.168.4.103 192.168.4.104 192.168.4.105 192.168.4.106 192.168.4.107 192.168.4.108 192.168.4.156"}
for h in $HOSTS; do
  timeout 10 ssh -o BatchMode=yes -o ConnectTimeout=5 $h 'for f in ~/chebyshev_gpu/*/status.json; do [ -f "$f" ] && echo "$(basename $(dirname $f)) $(cat ~/chebyshev_gpu/$(basename $(dirname $f))/ladder.m 2>/dev/null || echo -) $(cat $f)"; done' 2>/dev/null
done | python3 "$(dirname "$0")/status_fmt.py"
