#!/usr/bin/env bash
# T4/T5: failure + recovery of s2 (h5). MODE=kill (default, kills the server process) or MODE=link (takes s1-eth2 down).
set -u
cd "$(dirname "$0")/.." || exit 1
[ "$EUID" -eq 0 ] || exec sudo -E env "PATH=$PATH" bash "$0" "$@"
[ -f /tmp/lb_hosts/ready ] || { echo "topology not running (start ryu-manager, then sudo python3 topology/lb_topology.py)"; exit 2; }
hexec() { local h=$1; shift; mnexec -a "$(cat /tmp/lb_hosts/$h.pid)" "$@"; }
mkdir -p results/tmp
PREFIX=${LB_LOG_PREFIX:-results/controller}
MODE=${MODE:-kill}; D=${DURATION:-45}; KILL_AT=10; RESTART_AT=28
fail()    { if [ "$MODE" = link ]; then ip link set s1-eth2 down; else pkill -f "tcp_server.py --name s2"; fi; }
restore() { if [ "$MODE" = link ]; then ip link set s1-eth2 up
            else hexec h5 nohup python3 servers/tcp_server.py --name s2 --port 9000 > results/logs/s2_server.log 2>&1 & fi; }
T0=$(date +%s.%N)
for h in h1 h2 h3; do
  hexec $h python3 clients/client_generator.py --clients 17 --duration $D --csv results/tmp/fail_$h.csv > /dev/null &
done
sleep $KILL_AT;                 T_FAIL=$(date +%s.%N);    echo "failing s2 ($MODE) at +${KILL_AT}s"; fail
sleep $((RESTART_AT-KILL_AT));  T_RESTORE=$(date +%s.%N); echo "restoring s2 at +${RESTART_AT}s"; restore
wait
python3 - "$T0" "$T_FAIL" "$T_RESTORE" "$PREFIX" <<'PY'
import csv, glob, os, sys
t0, tf, tr = map(float, sys.argv[1:4]); prefix = sys.argv[4]
rows = [r for f in glob.glob("results/tmp/fail_h*.csv") for r in csv.DictReader(open(f))]
ev = list(csv.DictReader(open(prefix + "_events.csv"))) if os.path.exists(prefix + "_events.csv") else []
def phase(name, a, b):
    w = [r for r in rows if a <= float(r["ts"]) < b]
    ok = [r for r in w if r["status"] == "ok"]
    print(f"{name:<22} requests={len(w):4d} failed={len(w)-len(ok):3d} to_s2={sum(r['server']=='s2' for r in ok):4d}")
phase("before failure", t0, tf); phase("failure -> restore", tf, tr); phase("after restore", tr + 12, 1e18)
down = [float(e["ts"]) for e in ev if e["server"] == "s2" and e["new"] == "DOWN" and float(e["ts"]) >= tf]
up = [float(e["ts"]) for e in ev if e["server"] == "s2" and e["new"] == "UP" and float(e["ts"]) >= tr]
print("detection time: %s" % (f"{down[0]-tf:.1f}s" if down else "not detected"))
print("recovery time:  %s" % (f"{up[0]-tr:.1f}s" if up else "not recovered"))
mid = [r for r in rows if tf + 8 <= float(r["ts"]) < tr and r["status"] == "ok"]
good = bool(down) and not any(r["server"] == "s2" for r in mid)
print("T4", "PASS" if good else "FAIL", "| T5", "PASS" if up else "FAIL"); sys.exit(0 if good and up else 1)
PY
