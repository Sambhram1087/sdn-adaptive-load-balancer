#!/usr/bin/env bash
# T7: runs every scenario for static and adaptive and writes results/runs/*. Starts/stops ryu + topology itself.
# Env: DURATION (s per run, default 30), SCENARIOS (default: all), MODES (default "static adaptive"), RYU (ryu-manager path)
set -u
cd "$(dirname "$0")/.." || exit 1
RYU=${RYU:-$(command -v ryu-manager || true)}
[ -n "$RYU" ] || { echo "ryu-manager not found"; exit 1; }
[ "$EUID" -eq 0 ] || exec sudo -E env "PATH=$PATH" RYU="$RYU" bash "$0" "$@"
DURATION=${DURATION:-30}
SCENARIOS=${SCENARIOS:-"normal hetero conc_10 conc_100 failure congestion"}
MODES=${MODES:-"static adaptive"}
RES=results/runs; mkdir -p $RES results/logs
hexec() { local h=$1; shift; mnexec -a "$(cat /tmp/lb_hosts/$h.pid)" "$@"; }

cleanup() {
  [ -n "${TOPO_PID:-}" ] && kill -INT "$TOPO_PID" 2>/dev/null && wait "$TOPO_PID" 2>/dev/null
  [ -n "${RYU_PID:-}" ] && kill "$RYU_PID" 2>/dev/null && wait "$RYU_PID" 2>/dev/null
  pkill -f "servers/tcp_server.py" 2>/dev/null; pkill -f "monitoring/server_reporter.py" 2>/dev/null
  pkill -x iperf3 2>/dev/null
  mn -c > /dev/null 2>&1; rm -rf /tmp/lb_hosts /tmp/lb_s*.ctl /tmp/lb_s*.stats
  TOPO_PID=""; RYU_PID=""
}
trap 'cleanup; exit 130' INT TERM

start_stack() {  # mode tag
  cleanup
  rm -f $RES/$2_stats.csv $RES/$2_events.csv
  LB_MODE=$1 LB_LOG_PREFIX=$RES/$2 "$RYU" controller/lb_controller.py > $RES/$2_ryu.log 2>&1 & RYU_PID=$!
  sleep 3
  python3 topology/lb_topology.py --no-cli > $RES/$2_topo.log 2>&1 & TOPO_PID=$!
  for _ in $(seq 60); do [ -f /tmp/lb_hosts/ready ] && break; sleep 1; done
  [ -f /tmp/lb_hosts/ready ] || { echo "topology failed, see $RES/$2_topo.log"; return 1; }
  sleep 4  # let reporters register with the controller
}

run_one() {  # scenario mode
  local sc=$1 mode=$2 tag="${1}_${2}" n=50 hosts="h1 h2 h3" pids=()
  case $sc in conc_*) n=${sc#conc_};; esac
  if [ "$sc" = congestion ]; then
    command -v iperf3 > /dev/null || { echo "  iperf3 missing; skipping $sc"; return 0; }
    hosts="h1 h2"
  fi
  echo "== $sc / $mode ($n clients, ${DURATION}s)"
  start_stack "$mode" "$tag" || return 1
  local per=$(( (n + $(echo $hosts | wc -w) - 1) / $(echo $hosts | wc -w) ))
  [ "$sc" = hetero ] && echo "150 15" > /tmp/lb_s2.ctl   # s2 slowed + CPU-heavy
  if [ "$sc" = congestion ]; then
    hexec h4 iperf3 -s -D > /dev/null 2>&1; sleep 1
    hexec h3 iperf3 -c 10.0.0.4 -t "$DURATION" > $RES/${tag}_iperf.txt 2>&1 & pids+=($!)
  fi
  for h in $hosts; do
    hexec $h python3 clients/client_generator.py --clients $per --duration "$DURATION" \
      --csv $RES/${tag}_$h.csv --summary $RES/${tag}_$h.json --label $h > /dev/null & pids+=($!)
  done
  if [ "$sc" = failure ]; then
    sleep $((DURATION / 3)); date +%s.%N > $RES/${tag}_kill.txt
    pkill -f "tcp_server.py --name s2"
  fi
  wait "${pids[@]}"
  cp results/logs/s*_server.log $RES/ 2>/dev/null
  cleanup
}

for sc in $SCENARIOS; do for m in $MODES; do run_one "$sc" "$m"; done; done
cleanup
echo "done; generating plots"
python3 results/plot_results.py
