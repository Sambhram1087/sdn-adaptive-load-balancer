#!/usr/bin/env python3
"""Plain-language results dashboard.  Run:  uvicorn webui.app:app --reload   (from the repo root)
Reads results/runs/*.csv (produced by tests/run_comparison.sh) - no Mininet/Ryu needed to view."""
import glob
import math
import os
import sys
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "results"))
import plot_results as pr  # reuse loader + per-run statistics

if os.environ.get("LB_RUNS_DIR"):
    pr.RUNS = os.environ["LB_RUNS_DIR"]
    pr.HERE = os.path.dirname(os.path.abspath(pr.RUNS))  # plots/summary are written next to runs/

PLOTS = {"response_time", "load_distribution"}
_plot_lock = threading.Lock()

EXPLAIN = {
    "normal": "Everyone is served by three equally fast servers.",
    "hetero": "One server is made deliberately slow (+300 ms).",
    "conc_10": "10 users hit the service at the same time.",
    "conc_100": "100 users hit the service at the same time.",
    "failure": "One server crashes partway through the test.",
    "congestion": "The network link to one server is jammed with other traffic.",
}

app = FastAPI(title="SDN Load Balancer Results")


def _num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def _verdict(s, a):
    """Plain-language verdict from avg latency and failures."""
    if a["fail_pct"] + 1 < s["fail_pct"]:
        return "better", f"Far fewer failed requests ({s['fail_pct']:.0f}% → {a['fail_pct']:.0f}%)."
    if a["fail_pct"] > s["fail_pct"] + 1:
        return "worse", f"More requests failed ({s['fail_pct']:.0f}% → {a['fail_pct']:.0f}%)."
    if not s["avg_ms"]:
        return "same", "Not enough data."
    chg = (s["avg_ms"] - a["avg_ms"]) / s["avg_ms"] * 100
    if chg >= 5:
        return "better", f"Users got answers {chg:.0f}% faster."
    if chg <= -5:
        return "worse", f"Users waited {-chg:.0f}% longer."
    return "same", "About the same speed - nothing to fix in this situation."


def _share(row, servers):
    tot = sum(row.get("n_" + s, 0) or 0 for s in servers)
    return {s: round(100 * (row.get("n_" + s, 0) or 0) / tot, 1) if tot else 0 for s in servers}


@app.get("/api/results")
def results():
    df = pr.load()
    if df.empty:
        return {"empty": True, "scenarios": []}
    runs = {(sc, m): {k: _num(v) if not isinstance(v, str) else v for k, v in pr.stats_for(g, sc, m).items()}
            for (sc, m), g in df.groupby(["scenario", "mode"])}
    servers = sorted({k[2:] for r in runs.values() for k in r if k.startswith("n_")})
    names = [s for s in pr.ORDER if any(k[0] == s for k in runs)] + \
            sorted({k[0] for k in runs} - set(pr.ORDER))
    out = []
    for sc in names:
        s, a = runs.get((sc, "static")), runs.get((sc, "adaptive"))
        item = {"id": sc, "title": pr.LABEL.get(sc, sc), "about": EXPLAIN.get(sc, ""),
                "static": s, "adaptive": a}
        if s and a:
            item["verdict"], item["summary"] = _verdict(s, a)
            item["share"] = {"static": _share(s, servers), "adaptive": _share(a, servers)}
            if a.get("detection_s") is not None:
                item["note"] = f"The adaptive balancer noticed the crashed server after {a['detection_s']:.1f} s."
        out.append(item)
    comp = [i for i in out if "verdict" in i]
    tally = {v: sum(i["verdict"] == v for i in comp) for v in ("better", "same", "worse")}
    return {"empty": False, "servers": servers, "scenarios": out, "tally": tally}


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "index.html"))


@app.get("/plots/{name}.png")
def plot(name: str):
    """Serve the matplotlib charts; (re)generate them with plot_results.main() when missing or older than the data."""
    if name not in PLOTS:
        raise HTTPException(404)
    path = os.path.join(pr.HERE, name + ".png")
    with _plot_lock:
        newest = max((os.path.getmtime(f) for f in glob.glob(os.path.join(pr.RUNS, "*.csv"))), default=0)
        if not os.path.exists(path) or os.path.getmtime(path) < newest:
            try:
                pr.main()
            except SystemExit:
                raise HTTPException(404, "no run data yet")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})
