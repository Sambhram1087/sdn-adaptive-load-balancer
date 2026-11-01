#!/usr/bin/env python3
"""Aggregate results/runs/*_<mode>_h<N>.csv into summary.csv/summary.md and plots.

Outputs (in results/): summary.csv, summary.md, response_time.png, load_distribution.png
"""
import glob
import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
ORDER = ["normal", "hetero", "conc_10", "conc_100", "failure", "congestion"]
LABEL = {"normal": "Normal load", "hetero": "One slow server", "conc_10": "10 clients",
         "conc_100": "100 clients", "failure": "Server failure", "congestion": "Link congestion"}
COLORS = {"static": "#888888", "adaptive": "#1f77b4"}


def load():
    frames = []
    for f in glob.glob(os.path.join(RUNS, "*_h*.csv")):
        m = re.match(r"^(.+)_(static|adaptive)_(h\d+)\.csv$", os.path.basename(f))
        if not m:
            continue
        df = pd.read_csv(f)
        df["scenario"], df["mode"] = m.group(1), m.group(2)
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def stats_for(df, sc, mode):
    ok = df[df.status == "ok"]
    span = max(df.ts.max() - df.ts.min(), 1e-9)
    r = {"scenario": sc, "mode": mode, "requests": len(df),
         "avg_ms": ok.latency_ms.mean(), "p95_ms": ok.latency_ms.quantile(0.95),
         "throughput_rps": len(ok) / span, "fail_pct": 100.0 * (len(df) - len(ok)) / len(df)}
    for s, c in ok.server.value_counts().items():
        r["n_" + s] = int(c)
    sp = os.path.join(RUNS, f"{sc}_{mode}_stats.csv")
    if os.path.exists(sp):
        st = pd.read_csv(sp)
        if len(st) > 1:
            r["packet_in_per_s"] = (st.packet_in_total.iloc[-1] - st.packet_in_total.iloc[0]) / \
                max(st.ts.iloc[-1] - st.ts.iloc[0], 1e-9)
    kp, ep = os.path.join(RUNS, f"{sc}_{mode}_kill.txt"), os.path.join(RUNS, f"{sc}_{mode}_events.csv")
    if os.path.exists(kp) and os.path.exists(ep):
        kill = float(open(kp).read().strip())
        ev = pd.read_csv(ep)
        d = ev[(ev.server == "s2") & (ev.new == "DOWN") & (ev.ts >= kill)]
        if len(d):
            r["detection_s"] = d.ts.iloc[0] - kill
    return r


def main():
    df = load()
    if df.empty:
        raise SystemExit("no run data in results/runs/ (run tests/run_comparison.sh first)")
    rows = [stats_for(g, sc, mode) for (sc, mode), g in df.groupby(["scenario", "mode"])]
    sm = pd.DataFrame(rows)
    sm["_o"] = sm.scenario.map(lambda s: ORDER.index(s) if s in ORDER else 99)
    sm = sm.sort_values(["_o", "scenario", "mode"]).drop(columns="_o")
    sm.round(2).to_csv(os.path.join(HERE, "summary.csv"), index=False)

    # markdown comparison table (README section 11)
    lines = ["| Scenario | Metric | Static | Adaptive | Improvement |", "|---|---|---|---|---|"]
    for sc in sm.scenario.unique():
        s = sm[(sm.scenario == sc) & (sm["mode"] == "static")]
        a = sm[(sm.scenario == sc) & (sm["mode"] == "adaptive")]
        if s.empty or a.empty:
            continue
        for col, name, unit, lower_better in (("avg_ms", "Avg response time", "ms", True),
                                              ("p95_ms", "95th pct latency", "ms", True),
                                              ("fail_pct", "Failed requests", "%", True),
                                              ("throughput_rps", "Throughput", "req/s", False)):
            sv, av = float(s[col].iloc[0]), float(a[col].iloc[0])
            imp = ((sv - av) if lower_better else (av - sv)) / sv * 100 if sv else float("nan")
            lines.append(f"| {LABEL.get(sc, sc)} | {name} | {sv:.1f} {unit} | {av:.1f} {unit} | {imp:.0f} % |" if imp == imp else f"| {LABEL.get(sc, sc)} | {name} | {sv:.1f} {unit} | {av:.1f} {unit} | n/a |")
    open(os.path.join(HERE, "summary.md"), "w").write("\n".join(lines) + "\n")

    scs = [s for s in ORDER if s in set(sm.scenario)] + sorted(set(sm.scenario) - set(ORDER))
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
    for ax, col, title in zip(axes, ("avg_ms", "p95_ms"), ("Average response time (ms)", "95th percentile (ms)")):
        for i, mode in enumerate(("static", "adaptive")):
            vals = [sm[(sm.scenario == s) & (sm["mode"] == mode)][col].mean() for s in scs]
            ax.bar([x + (i - 0.5) * 0.38 for x in range(len(scs))], vals, 0.38, label=mode, color=COLORS[mode])
        ax.set_xticks(range(len(scs)))
        ax.set_xticklabels([LABEL.get(s, s) for s in scs], rotation=25, ha="right")
        ax.set_title(title)
        ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "response_time.png"), dpi=130)

    servers = sorted(c[2:] for c in sm.columns if c.startswith("n_"))
    fig, ax = plt.subplots(figsize=(max(8, 1.4 * len(scs) * 2), 4.5))
    labels, bottoms = [], None
    xs = [(i, m) for i in range(len(scs)) for m in ("static", "adaptive")]
    for srv in servers:
        vals = []
        for i, m in xs:
            r = sm[(sm.scenario == scs[i]) & (sm["mode"] == m)]
            tot = sum(float(r.get("n_" + x, pd.Series([0])).fillna(0).iloc[0]) for x in servers) if len(r) else 0
            vals.append(100.0 * float(r.get("n_" + srv, pd.Series([0])).fillna(0).iloc[0]) / tot if tot else 0)
        ax.bar(range(len(xs)), vals, bottom=bottoms, label=srv)
        bottoms = vals if bottoms is None else [b + v for b, v in zip(bottoms, vals)]
    ax.set_xticks(range(len(xs)))
    ax.set_xticklabels([f"{LABEL.get(scs[i], scs[i])}\n{m}" for i, m in xs], rotation=40, ha="right", fontsize=8)
    ax.set_ylabel("% of served requests")
    ax.set_title("Per-server load distribution")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "load_distribution.png"), dpi=130)
    print(sm.round(2).to_string(index=False))
    print("\nwrote summary.csv, summary.md, response_time.png, load_distribution.png in", HERE)


if __name__ == "__main__":
    main()
