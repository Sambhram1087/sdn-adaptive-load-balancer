#!/usr/bin/env bash
# Failure: kill s2's server mid-run. Adaptive mode must detect DOWN within the timeout and stop routing to s2.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_root
trap stop_env EXIT
P="$TESTRUNS/failure_adaptive"
TIMEOUT=$(python3 -c "import yaml;print(yaml.safe_load(open('$ROOT/config/config.yaml'))['monitoring']['failure_timeout'])")

start_env adaptive "$P" || exit 1
run_clients "$P" 9 40 0.1 &
CP=$!
sleep 12
date +%s.%N > "${P}_kill.txt"
pkill -f "tcp_server.py.*--name s2"
echo "killed s2 server"
wait "$CP"

read -r DET LATE_S2 LATE_FAIL < <(python3 - "$P" <<'EOF'
import glob, sys
import pandas as pd
p = sys.argv[1]
kill = float(open(p + "_kill.txt").read())
ev = pd.read_csv(p + "_events.csv")
d = ev[(ev.server == "s2") & (ev.new == "DOWN") & (ev.ts >= kill)]
det = float(d.ts.iloc[0] - kill) if len(d) else -1.0
df = pd.concat(pd.read_csv(f) for f in glob.glob(p + "_h*.csv"))
late = df[df.ts > kill + (det if det >= 0 else 1e9) + 2]
print(round(det, 2), int(((late.server == "s2") & (late.status == "ok")).sum()),
      round(100 * float((late.status != "ok").mean()), 2) if len(late) else 100.0)
EOF
)
echo "detection=${DET}s  s2_served_after_detection=$LATE_S2  late_fail_pct=$LATE_FAIL"

check "s2 detected DOWN"                  python3 -c "import sys; sys.exit(0 if $DET >= 0 else 1)"
check "detected within timeout + 3 s"     python3 -c "import sys; sys.exit(0 if 0 <= $DET <= $TIMEOUT + 3 else 1)"
check "no traffic to s2 after detection"  [ "$LATE_S2" -eq 0 ]
check "failures after detection < 5%"     python3 -c "import sys; sys.exit(0 if $LATE_FAIL < 5 else 1)"
finish
