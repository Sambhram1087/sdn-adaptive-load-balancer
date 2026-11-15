# Results Directory

This directory holds outputs produced by test runs and the plotting script.

## Generated Files

| File / Pattern | Produced by | Description |
|----------------|-------------|-------------|
| `runs/<scenario>_<mode>_h{1,2,3}.csv` | `tests/run_comparison.sh` | Per-request latency log (ts, client, req, server, latency_ms, status) |
| `runs/<scenario>_<mode>_h{1,2,3}.json` | `tests/run_comparison.sh` | Per-host summary JSON (total, ok, per_server counts, percentiles) |
| `runs/<scenario>_<mode>_stats.csv` | `controller/lb_controller.py` | Per-poll server metrics (assigned, conns, cpu, util, score, status) |
| `runs/<scenario>_<mode>_events.csv` | `controller/lb_controller.py` | State-transition events (server, old, new, reason, score) |
| `runs/<scenario>_<mode>_kill.txt` | `tests/test_failure.sh` / `run_comparison.sh` | UNIX timestamp (float) when server was killed (failure scenario only) |
| `test_runs/` | `tests/test_*.sh` | Same structure as `runs/` but for pass/fail unit tests (not aggregated into plots) |
| `logs/` | `tests/lib.sh` | `ryu_<mode>.log`, `topology.log`, `<server>_server.log`, `<server>_reporter.log` |
| `summary.csv` | `results/plot_results.py` | Aggregated per-scenario/mode statistics table |
| `summary.md` | `results/plot_results.py` | Markdown comparison table (static vs adaptive) |
| `response_time.png` | `results/plot_results.py` | Bar chart: avg and p95 latency per scenario |
| `load_distribution.png` | `results/plot_results.py` | Stacked bar chart: % requests served per backend |

> **Note:** Everything under `runs/`, `test_runs/`, `logs/`, and all generated
> `.csv`, `.json`, `.png`, `.md` files are **git-ignored** (runtime artefacts).
> Only the scripts and this README are tracked.

## Scenarios

| Scenario | Description |
|----------|-------------|
| `normal` | 9 clients, baseline load with equal-latency servers |
| `hetero` | Same as `normal` but `s1` has +300 ms extra sleep (heterogeneous servers) |
| `conc_10` | 10 concurrent clients |
| `conc_100` | 100 concurrent clients (high concurrency stress test) |
| `failure` | 9 clients; `s2`'s server process is killed at ⅓ of the run duration |
| `congestion` | 6 clients; `s1`'s link is saturated with an iperf background flow |

## Quickstart

```bash
# Run all scenarios and generate plots (requires sudo, Mininet, Ryu):
sudo tests/run_comparison.sh

# Generate plots from existing run data:
python3 results/plot_results.py
```

Output files appear in this directory after the plotting script finishes.
