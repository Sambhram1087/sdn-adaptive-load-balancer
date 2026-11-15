#!/usr/bin/env bash
# Overload: s1 is made slow + CPU-heavy under 60 clients. Adaptive mode must mark it OVERLOADED and shift load away.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_root
trap stop_env EXIT
P="$TESTRUNS/overload_adaptive"

start_env adaptive "$P" || exit 1
echo "300 80" > /tmp/lb_s1.slow      # +300 ms sleep, 80 ms CPU burn per request on s1
run_clients "$P" 60 30 0.05
read -r total ok n1 n2 n3 < <(summ "${P}"_h*.json)
echo "total=$total ok=$ok s1=$n1 s2=$n2 s3=$n3"

check "requests were sent"       [ "$total" -gt 0 ]
check "s1 marked OVERLOADED"     grep -q ',s1,UP,OVERLOADED,' "${P}_events.csv"
check "s1 served least traffic"  [ "$n1" -lt "$n2" -a "$n1" -lt "$n3" ]
check "failures under 10%"       [ $((ok * 100)) -ge $((total * 90)) ]
finish
