# results/runs/

This directory stores per-scenario, per-mode run data produced by the test scripts.

## File Naming Convention

```
<scenario>_<mode>_h<N>.csv      # per-request log from client host h<N>
<scenario>_<mode>_h<N>.json     # summary JSON from client host h<N>
<scenario>_<mode>_stats.csv     # controller port-stats poll log
<scenario>_<mode>_events.csv    # server state-transition events
<scenario>_<mode>_kill.txt      # kill timestamp (failure scenario only)
```

Where:
- `<scenario>` ∈ `normal`, `hetero`, `conc_10`, `conc_100`, `failure`, `congestion`
- `<mode>` ∈ `static`, `adaptive`
- `<N>` ∈ `1`, `2`, `3` (Mininet client hosts)

## Populated by

```bash
sudo tests/run_comparison.sh          # all scenarios, both modes
sudo DUR=60 tests/run_comparison.sh   # longer runs (60 s per scenario)
sudo tests/run_comparison.sh failure  # single scenario
```

## Consumed by

```bash
python3 results/plot_results.py
```

Produces `results/summary.csv`, `results/summary.md`,
`results/response_time.png`, and `results/load_distribution.png`.

> This directory is intentionally empty in the repository.
> All CSV/JSON files are runtime artefacts and are git-ignored.
