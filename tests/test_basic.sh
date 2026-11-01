#!/usr/bin/env bash
# T1/T2: basic connectivity and distribution. Requires controller + topology already running.
set -u
cd "$(dirname "$0")/.." || exit 1
[ "$EUID" -eq 0 ] || exec sudo -E env "PATH=$PATH" bash "$0" "$@"
[ -f /tmp/lb_hosts/ready ] || { echo "topology not running (start ryu-manager, then sudo python3 topology/lb_topology.py)"; exit 2; }
hexec() { local h=$1; shift; mnexec -a "$(cat /tmp/lb_hosts/$h.pid)" "$@"; }
mkdir -p results/tmp
for h in h1 h2 h3; do
  hexec $h python3 clients/client_generator.py --clients 17 --duration "${DURATION:-8}" \
    --csv results/tmp/basic_$h.csv --summary results/tmp/basic_$h.json --label $h > /dev/null &
done
wait
python3 - <<'PY'
import glob, json, sys
tot, bad, ps = 0, False, {}
for f in sorted(glob.glob("results/tmp/basic_h*.json")):
    d = json.load(open(f)); tot += d["ok"]
    print(f"{d['label']}: ok={d['ok']} failed={d['failed']} avg={d['avg_ms']}ms p95={d['p95_ms']}ms servers={d['per_server']}")
    bad |= d["ok"] == 0 or d["fail_pct"] > 1.0
    for k, v in d["per_server"].items(): ps[k] = ps.get(k, 0) + v
print("distribution:", ps)
share = [v / tot for v in ps.values()] if tot else []
if len(ps) < 3 or (share and max(share) > 0.5): print("WARN: distribution is uneven (T2)")
print("T1", "FAIL" if bad or not tot else "PASS"); sys.exit(1 if bad or not tot else 0)
PY
