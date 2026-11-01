#!/usr/bin/env bash
# T3: overload. Throttles s1 mid-run; expects controller to mark it OVERLOADED and avoid it. Run controller in adaptive mode.
set -u
cd "$(dirname "$0")/.." || exit 1
[ "$EUID" -eq 0 ] || exec sudo -E env "PATH=$PATH" bash "$0" "$@"
[ -f /tmp/lb_hosts/ready ] || { echo "topology not running (start ryu-manager, then sudo python3 topology/lb_topology.py)"; exit 2; }
hexec() { local h=$1; shift; mnexec -a "$(cat /tmp/lb_hosts/$h.pid)" "$@"; }
mkdir -p results/tmp
PREFIX=${LB_LOG_PREFIX:-results/controller}
D=${DURATION:-45}; T0=$(date +%s.%N)
for h in h1 h2 h3; do
  hexec $h python3 clients/client_generator.py --clients 17 --duration $D --csv results/tmp/over_$h.csv > /dev/null &
done
sleep 10; THROTTLE=$(date +%s.%N); echo "throttling s1 at +10s (delay 300ms, 30ms CPU/request)"
echo "300 30" > /tmp/lb_s1.ctl
wait; rm -f /tmp/lb_s1.ctl
python3 - "$T0" "$THROTTLE" "$PREFIX" <<'PY'
import csv, glob, sys
t0, tt, prefix = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
rows = [r for f in glob.glob("results/tmp/over_h*.csv") for r in csv.DictReader(open(f)) if r["status"] == "ok"]
def share(a, b):
    w = [r for r in rows if a <= float(r["ts"]) < b]
    return (sum(r["server"] == "s1" for r in w) / len(w) * 100) if w else 0.0
before, after = share(t0, tt), share(tt + 15, 1e18)
print(f"s1 share of requests: before throttle {before:.1f}%  |  after (+15s) {after:.1f}%")
ev = [r for r in csv.DictReader(open(prefix + "_events.csv")) if float(r["ts"]) >= tt] if glob.glob(prefix + "_events.csv") else []
for r in ev: print(f"  +{float(r['ts'])-tt:5.1f}s {r['server']} {r['old']}->{r['new']} ({r['reason']})")
ok = any(r["server"] == "s1" and r["new"] == "OVERLOADED" for r in ev) and after < before
print("T3", "PASS" if ok else "FAIL (is the controller in adaptive mode and thresholds tuned?)"); sys.exit(0 if ok else 1)
PY
