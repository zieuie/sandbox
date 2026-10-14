import json, sys, time
print(f"{'host':10} {'run':10} {'cell':7} {'target':>7} {'state':8} {'conflicts':>9} {'mean':>6} {'hours':>6} {'age':>6}")
for line in sys.stdin:
    run, m, js = line.split(" ", 2)
    s = json.loads(js)
    cell = f"({s['n']},{s['d']})"
    print(f"{s['host']:10} {run:10} {cell:7} {s['target_rows']:7} {s['state']:8} {s['best_conflicts']:9} {s['mean_conflicts']:6} "
          f"{s['elapsed_s'] / 3600:6.2f} {int(time.time()) - s['updated']:5}s")
