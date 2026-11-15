#!/usr/bin/env bash
# Shared helpers (sourced by test_*.sh and run_comparison.sh). Requires: sudo, mininet (mnexec), ryu-manager.
# If ryu-manager lives in a venv:  sudo env "PATH=$PATH" tests/test_basic.sh   (or RYU=/path/to/ryu-manager)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDS=/tmp/lb_hosts
RUNS="$ROOT/results/runs"            # data consumed by results/plot_results.py
TESTRUNS="$ROOT/results/test_runs"   # pass/fail tests write here so they don't pollute the plots
LOGS="$ROOT/results/logs"
RYU="${RYU:-ryu-manager}"
mkdir -p "$RUNS" "$TESTRUNS" "$LOGS"
VIP=$(python3 -c "import yaml;print(yaml.safe_load(open('$ROOT/config/config.yaml'))['vip'])")
PORT=$(python3 -c "import yaml;print(yaml.safe_load(open('$ROOT/config/config.yaml'))['service_port'])")
CTRL=""; TOPO=""; FAILS=0

need_root() {
  [ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
  for c in "$RYU" mnexec python3; do
    command -v "$c" >/dev/null || { echo "missing command: $c" >&2; exit 1; }
  done
}

hx() { mnexec -a "$(cat "$PIDS/$1.pid")" "${@:2}"; }   # run a command inside a Mininet host

stop_env() {
  [ -n "$TOPO" ] && { kill -TERM "$TOPO" 2>/dev/null; wait "$TOPO" 2>/dev/null; }
  [ -n "$CTRL" ] && { kill -TERM "$CTRL" 2>/dev/null; wait "$CTRL" 2>/dev/null; }
  TOPO=""; CTRL=""
  pkill -f "servers/tcp_server.py" 2>/dev/null; pkill -f "monitoring/server_reporter.py" 2>/dev/null
  pkill -x iperf 2>/dev/null
  mn -c >/dev/null 2>&1
  return 0
}

start_env() {  # $1 = static|adaptive   $2 = log prefix (<prefix>_stats.csv, <prefix>_events.csv)
  local mode=$1 prefix=$2
  stop_env
  rm -rf "$PIDS"
  rm -f "${prefix}"_stats.csv "${prefix}"_events.csv "${prefix}"_kill.txt "${prefix}"_h*.csv "${prefix}"_h*.json \
        /tmp/lb_s*.stats /tmp/lb_s*.slow
  LB_MODE=$mode LB_LOG_PREFIX=$prefix "$RYU" "$ROOT/controller/lb_controller.py" >"$LOGS/ryu_$mode.log" 2>&1 &
  CTRL=$!
  sleep 3
  python3 "$ROOT/topology/lb_topology.py" --no-cli >"$LOGS/topology.log" 2>&1 &
  TOPO=$!
  for _ in $(seq 60); do [ -f "$PIDS/ready" ] && break; sleep 1; done
  if [ ! -f "$PIDS/ready" ]; then
    echo "topology not ready; see $LOGS/topology.log and $LOGS/ryu_$mode.log" >&2
    stop_env; return 1
  fi
  sleep 4   # servers/reporters warm up and register with the controller
}

run_clients() {  # $1 = file prefix  $2 = total client threads  $3 = duration s  $4 = think s  (split over h1..h3)
  local prefix=$1 total=$2 dur=$3 think=$4 per rem i n pids=()
  per=$((total / 3)); rem=$((total % 3))
  for i in 1 2 3; do
    n=$((per + (i <= rem ? 1 : 0)))
    [ "$n" -gt 0 ] || continue
    hx h$i python3 "$ROOT/clients/client_generator.py" --vip "$VIP" --port "$PORT" --clients "$n" \
       --duration "$dur" --think "$think" --csv "${prefix}_h$i.csv" --summary "${prefix}_h$i.json" \
       --label "$(basename "$prefix")" >/dev/null &
    pids+=($!)
  done
  wait "${pids[@]}"
}

summ() {  # prints: total ok n_s1 n_s2 n_s3 from client summary JSONs
  python3 - "$@" <<'EOF'
import json, sys
t = ok = 0
per = {"s1": 0, "s2": 0, "s3": 0}
for f in sys.argv[1:]:
    s = json.load(open(f)); t += s["total"]; ok += s["ok"]
    for k, v in s["per_server"].items():
        per[k] = per.get(k, 0) + v
print(t, ok, per["s1"], per["s2"], per["s3"])
EOF
}

check() {  # check "description" cmd args...
  local d=$1; shift
  if "$@"; then echo "PASS  $d"; else echo "FAIL  $d"; FAILS=$((FAILS + 1)); fi
}

finish() { [ "$FAILS" -eq 0 ] && echo "ALL PASSED" || echo "$FAILS check(s) failed"; [ "$FAILS" -eq 0 ]; }
