#!/usr/bin/env bash
# Basic functionality: VIP reachable, all requests served, all 3 servers used, no server marked DOWN.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_root
trap stop_env EXIT
P="$TESTRUNS/basic_adaptive"

start_env adaptive "$P" || exit 1
run_clients "$P" 6 15 0.1
read -r total ok n1 n2 n3 < <(summ "${P}"_h*.json)
echo "total=$total ok=$ok s1=$n1 s2=$n2 s3=$n3"

check "requests were sent"            [ "$total" -gt 0 ]
check "all requests succeeded"        [ "$ok" -eq "$total" ]
check "all 3 servers received traffic" [ "$n1" -gt 0 -a "$n2" -gt 0 -a "$n3" -gt 0 ]
check "controller wrote stats"        [ "$(wc -l < "${P}_stats.csv")" -gt 1 ]
check "no server marked DOWN"         bash -c "! grep -q ',DOWN,' '${P}_events.csv'"
finish
