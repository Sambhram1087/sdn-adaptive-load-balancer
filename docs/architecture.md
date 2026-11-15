# SDN Adaptive Load Balancer — Architecture

## Overview

This project implements a **software-defined load balancer** running on [Ryu](https://ryu-sdn.org/) (OpenFlow 1.3)
and [Mininet](http://mininet.org/). It supports two operating modes selected at startup:

| Mode | Description |
|------|-------------|
| `static` | Plain round-robin across all servers; ignores load metrics and failures |
| `adaptive` | Load-aware selection with automatic failure detection and recovery |

---

## Component Map

```
Clients (h1–h3)
      │  TCP to VIP 10.0.0.100:9000
      ▼
  OVS Switch (s1)   ◄──── OpenFlow 1.3 ────► Ryu Controller (lb_controller.py)
      │                                              ▲
      │  ports 1–3                                   │ UDP load reports (port 9999)
      ▼                                              │
Servers (h4–h6)                           server_reporter.py (per server)
  tcp_server.py                                      │
  writes /tmp/lb_<name>.stats ──────────────────────┘
```

A **root-namespace interface** (`10.0.0.254`, switch port 7) lets the servers reach the
controller host for UDP heartbeat/load reports without routing through the VIP.

---

## Directory Structure

```
sdn-adaptive-load-balancer/
├── config/
│   └── config.yaml          # VIP, server list, thresholds, flow timeouts
├── controller/
│   └── lb_controller.py     # Ryu app: LBState (pure logic) + LBController (OpenFlow)
├── topology/
│   └── lb_topology.py       # Mininet topology setup (3 clients + 3 servers + OVS)
├── servers/
│   └── tcp_server.py        # Backend echo server; exposes /tmp/lb_<name>.stats
├── monitoring/
│   └── server_reporter.py   # Reads stats file + psutil CPU; sends UDP to controller
├── clients/
│   └── client_generator.py  # Concurrent TCP workload generator with CSV/JSON output
├── results/
│   ├── plot_results.py      # Aggregates run CSVs → summary.csv/md + PNG plots
│   └── runs/                # Per-run CSV data written by tests (git-ignored content)
├── docs/                    # Design documentation (this directory)
└── tests/
    ├── lib.sh               # Shared bash helpers (start_env, run_clients, check, …)
    ├── test_basic.sh        # Smoke test: VIP reachable, all servers used
    ├── test_failure.sh      # Failure detection: kill s2, verify DOWN within timeout
    ├── test_overload.sh     # Overload: inject slow+CPU load on s1, verify re-routing
    └── run_comparison.sh    # Full scenario suite (static vs adaptive) + plots
```

---

## Data Flow

### New TCP Connection (SYN)

1. Client SYN arrives at switch → table-miss → **PacketIn** to controller.
2. Controller calls `LBState.select()` to pick a backend.
3. Two **flow rules** (priority 10, cookie = server_idx+1) are installed:
   - **Forward**: rewrite `eth_dst`, `ipv4_dst` → server MAC/IP, output to server port.
   - **Reverse**: rewrite `eth_src`/`ipv4_src` → VIP MAC/IP, output to client port.
4. The SYN is immediately sent out via **PacketOut** (no second trip needed).

### Mid-Connection Packet (after flow expiry)

If the flow entry expires before the connection closes, subsequent packets hit the
controller again. The `flow_map` cache (`(client_ip, client_port) → server_idx`) ensures
the same backend is chosen, preventing connection disruption.

### Load Reports

Every `report_interval` seconds (default 1 s) each `server_reporter.py`:
1. Reads `/tmp/lb_<name>.stats` (written atomically by `tcp_server.py`).
2. Samples CPU via `psutil`.
3. Sends a UDP JSON datagram to `controller_ip:report_port`.

The controller's `_report_listener` greenlet receives these and calls `LBState.update_report()`.

---

## Scoring & Thresholds (Adaptive Mode)

Each server has a composite **load score** ∈ [0, 1]:

```
score = w_conn × (conns + pending) / max_conns
      + w_cpu  × cpu / 100
      + w_link × link_util
```

Default weights: `connections=0.4`, `cpu=0.3`, `link_utilization=0.3`.

| Transition | Trigger |
|------------|---------|
| UP → OVERLOADED | `score ≥ overload` for `consecutive_intervals` poll cycles |
| OVERLOADED → UP | `score < recovery` |
| Any → DOWN | Heartbeat timeout (`failure_timeout` s without a UDP report) or port-down event |
| DOWN → RECOVERING | A new UDP report is received (link back up) |
| RECOVERING → UP | `score < recovery` |

Server selection priority: **UP** > **OVERLOADED** > **RECOVERING**.  
Within a tier, the server with the lowest current score wins (round-robin breaks ties).

---

## Fault Injection

Write a file `/tmp/lb_<name>.slow` containing `<extra_sleep_ms> <cpu_burn_ms>` to
artificially slow down or CPU-stress a specific backend at runtime. Remove the file to
restore normal behaviour.

Example — make `s1` slow:
```bash
echo "300 80" > /tmp/lb_s1.slow   # +300 ms sleep + 80 ms busy-loop per request
rm /tmp/lb_s1.slow                 # restore
```

---

## Running

### Prerequisites

```bash
pip install -r requirements.txt    # ryu, eventlet, pyyaml, psutil, matplotlib, pandas
# Also: mininet, openvswitch-switch (system packages, need sudo)
```

### Start the controller + topology

```bash
# Terminal 1 — Ryu controller
LB_MODE=adaptive ryu-manager controller/lb_controller.py

# Terminal 2 — Mininet topology (requires sudo)
sudo python3 topology/lb_topology.py
```

### Run the test suite

```bash
sudo tests/test_basic.sh
sudo tests/test_failure.sh
sudo tests/test_overload.sh

# Full static vs adaptive comparison + plots:
sudo tests/run_comparison.sh
python3 results/plot_results.py
```

Pass `LB_MODE=static` (or `--mode static` via env) to compare against round-robin.

---

## Configuration Reference (`config/config.yaml`)

| Key | Default | Description |
|-----|---------|-------------|
| `vip` | `10.0.0.100` | Virtual IP clients connect to |
| `vip_mac` | `00:00:00:00:01:00` | MAC advertised in ARP replies |
| `service_port` | `9000` | TCP port of the service |
| `mode` | `adaptive` | Default mode (`static` \| `adaptive`); overridden by `LB_MODE` env |
| `monitoring.stats_interval` | `2` | Seconds between OpenFlow port-stats polls |
| `monitoring.report_interval` | `1` | Seconds between server UDP heartbeats |
| `monitoring.failure_timeout` | `5` | Seconds without heartbeat before DOWN |
| `monitoring.controller_ip` | `10.0.0.254` | Root-namespace IP servers send reports to |
| `monitoring.report_port` | `9999` | UDP port the controller listens on |
| `thresholds.overload` | `0.55` | Score above which a server is considered overloaded |
| `thresholds.recovery` | `0.35` | Score below which an overloaded server recovers |
| `thresholds.consecutive_intervals` | `3` | Poll cycles above overload before transition |
| `weights.connections` | `0.4` | Connection count weight in score |
| `weights.cpu` | `0.3` | CPU utilisation weight in score |
| `weights.link_utilization` | `0.3` | Link utilisation weight in score |
| `normalization.max_connections` | `10` | Connections that map to normalised score = 1.0 |
| `normalization.link_capacity_mbps` | `10` | Link capacity (must match `--bw` in topology) |
| `flow.idle_timeout` | `10` | OpenFlow flow idle timeout (seconds) |
| `flow.hard_timeout` | `60` | OpenFlow flow hard timeout (seconds) |
