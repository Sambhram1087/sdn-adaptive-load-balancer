#!/usr/bin/env python3
"""Sends UDP load reports (also serve as heartbeats) for one server to the controller.

Reads /tmp/lb_<name>.stats written by servers/tcp_server.py. If the server is dead or its stats are
stale, nothing is sent, so the controller's failure timeout fires.
"""
import argparse, json, os, socket, sys, time
import psutil, yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--config", default=os.path.join(HERE, "..", "config", "config.yaml"))
    ap.add_argument("--controller", help="override controller IP")
    ap.add_argument("--port", type=int, help="override report UDP port")
    ap.add_argument("--interval", type=float, help="override report interval (s)")
    ap.add_argument("--stats-dir", default="/tmp")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config))
    mon = cfg["monitoring"]
    dest = (a.controller or mon["controller_ip"], a.port or int(mon["report_port"]))
    interval = a.interval or float(mon["report_interval"])
    me = next((s for s in cfg["servers"] if s["name"] == a.name), None)
    if me is None:
        sys.exit(f"unknown server {a.name}")
    path = os.path.join(a.stats_dir, f"lb_{a.name}.stats")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    proc, seq = None, 0
    while True:
        t0 = time.time()
        try:
            st = json.load(open(path))
            if t0 - st["ts"] < max(3 * interval, 2.0):
                if proc is None or proc.pid != st["pid"]:
                    proc = psutil.Process(st["pid"])
                    proc.cpu_percent(None)  # prime
                cpu = min(100.0, max(0.0, proc.cpu_percent(None)))
                seq += 1
                msg = {"name": a.name, "ip": me["ip"], "ts": t0, "seq": seq,
                       "conns": st["active"], "cpu": round(cpu, 1), "svc_ms": st["avg_ms"]}
                sock.sendto(json.dumps(msg).encode(), dest)
        except (OSError, ValueError, KeyError, psutil.Error):
            proc = None
        time.sleep(max(0.0, interval - (time.time() - t0)))


if __name__ == "__main__":
    main()
