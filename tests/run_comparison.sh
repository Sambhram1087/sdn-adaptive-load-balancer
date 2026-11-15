#!/usr/bin/env bash
# Runs every scenario in static and adaptive mode, then plots.   Usage: sudo tests/run_comparison.sh [scenario ...]
# DUR=<seconds> per scenario (default 30). Output: results/runs/<scenario>_<mode>_{h1..h3.csv,stats.csv,events.csv,kill.txt}
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_root
trap stop_env EXIT
DUR="${DUR:-30}"
SCENARIOS=("$@"); [ ${#SCENARIOS[@]} -gt 0 ] || SCENARIOS=(normal hetero conc_10 conc_100 failure congestion)

scenario() {  # $1 = name  $2 = file prefix
  case $1 in
    normal)   run_clients "$2" 9 "$DUR" 0.1 ;;
    hetero)   echo "300 0" > /tmp/lb_s1.slow; run_clients "$2" 9 "$DUR" 0.1 ;;      # s1 slow
    conc_10)  run_clients "$2" 10 "$DUR" 0.1 ;;
    conc_100) run_clients "$2" 100 "$DUR" 0.1 ;;
    failure)  run_clients "$2" 9 "$DUR" 0.1 & local cp=$!
              sleep $((DUR / 3)); date +%s.%N > "${2}_kill.txt"; pkill -f "tcp_server.py.*--name s2"
              wait "$cp" ;;
    congestion)
              if command -v iperf >/dev/null; then       # saturate the s1 link (10.0.0.4) with background traffic
                hx h4 iperf -s >/dev/null 2>&1 &
                sleep 1
                hx h3 iperf -c 10.0.0.4 -t "$DUR" >/dev/null 2>&1 &
              else echo "WARNING: iperf not installed; congestion scenario has no background load" >&2; fi
              run_clients "$2" 6 "$DUR" 0.1
              pkill -x iperf 2>/dev/null ;;
    *) echo "unknown scenario $1" >&2; return 1 ;;
  esac
}

for mode in static adaptive; do
  for sc in "${SCENARIOS[@]}"; do
    echo "=== $sc / $mode"
    P="$RUNS/${sc}_${mode}"
    start_env "$mode" "$P" || exit 1
    scenario "$sc" "$P"
    stop_env
  done
done
python3 "$ROOT/results/plot_results.py"
