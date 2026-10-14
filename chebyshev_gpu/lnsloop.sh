#!/bin/bash
# SAT repair alongside a ladder (run inside the ladder's directory).
# Whenever the ladder's current try leaves a new pa_<n>_<d>_gpu_best.txt, try
# to repair it with symsat.py (freeing F1, then F2 orbits around the
# conflicts).  On success the ladder is stopped, ladder.m is raised by STEP
# and the ladder is restarted from ladder.cmd.
# Usage: lnsloop.sh n d step F1 F2 timeout_secs
n=$1 d=$2 step=$3 f1=$4 f2=$5 to=$6
last=0
while true; do
  b=pa_${n}_${d}_gpu_best.txt
  if [ -f "$b" ] && [ $(stat -c %Y "$b") -gt $last ]; then
    last=$(stat -c %Y "$b"); m=$(cat ladder.m); cp "$b" lns_fix.txt
    for F in $f1 $f2; do
      echo "=== $(date '+%F %T') repair m=$m free=$F"
      before=$(ls pa_${n}_${d}_sat_*.txt 2>/dev/null | wc -l)
      nice -n 19 ../.venv/bin/python ../symsat.py $n $d --active $m --fix lns_fix.txt --free $F --timeout $to | grep -E "fix:|result|verified"
      if [ $(ls pa_${n}_${d}_sat_*.txt 2>/dev/null | wc -l) -gt $before ]; then
        L=$(pgrep -f "^/bin/bash ./ladder.sh $n $d ")
        [ -n "$L" ] && kill -TERM $L && while kill -0 $L 2>/dev/null; do sleep 2; done
        echo $(( m + step )) > ladder.m
        echo "=== solved m=$m by SAT; ladder restarted at $(( m + step ))"
        (setsid nohup bash ladder.cmd >> log.txt 2>&1 < /dev/null &)
        break
      fi
    done
  fi
  sleep 120
done
