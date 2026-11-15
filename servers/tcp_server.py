#!/usr/bin/env python3
"""Backend TCP server behind the VIP. Protocol: client sends "REQ <id>\\n", server replies "OK <name> <id>\\n".

Writes /tmp/lb_<name>.stats (JSON: ts, pid, active, avg_ms) twice a second; monitoring/server_reporter.py
forwards it to the controller. If this process dies the file goes stale and the reports stop (failure detection).

Fault injection (read on every request): /tmp/lb_<name>.slow containing "<extra_sleep_ms> <cpu_burn_ms>".
"""
import argparse
import json
import os
import socket
import threading
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--service-ms", type=float, default=20.0, help="base service time per request")
    ap.add_argument("--stats-dir", default="/tmp")
    a = ap.parse_args()

    stats_path = os.path.join(a.stats_dir, f"lb_{a.name}.stats")
    slow_path = os.path.join(a.stats_dir, f"lb_{a.name}.slow")
    lock = threading.Lock()
    st = {"active": 0, "avg_ms": 0.0}

    def slow():
        try:
            sleep_ms, burn_ms = (float(x) for x in open(slow_path).read().split()[:2])
            return max(0.0, sleep_ms), max(0.0, burn_ms)
        except (OSError, ValueError):
            return 0.0, 0.0

    def handle(conn):
        t0 = time.time()
        with lock:
            st["active"] += 1
        try:
            conn.settimeout(5)
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = conn.recv(1024)
                if not chunk:
                    return
                buf += chunk
            sleep_ms, burn_ms = slow()
            end = time.perf_counter() + burn_ms / 1000.0
            while time.perf_counter() < end:  # busy loop => visible CPU load
                pass
            time.sleep((a.service_ms + sleep_ms) / 1000.0)
            parts = buf.split()
            rid = parts[1].decode(errors="replace") if len(parts) > 1 else "-"
            conn.sendall(f"OK {a.name} {rid}\n".encode())
        except OSError:
            pass
        finally:
            conn.close()
            dt = (time.time() - t0) * 1000.0
            with lock:
                st["active"] -= 1
                st["avg_ms"] = dt if st["avg_ms"] == 0 else 0.8 * st["avg_ms"] + 0.2 * dt

    def writer():
        tmp = stats_path + ".tmp"
        while True:
            with lock:
                snap = {"ts": time.time(), "pid": os.getpid(), "active": st["active"],
                        "avg_ms": round(st["avg_ms"], 2)}
            try:
                with open(tmp, "w") as f:
                    json.dump(snap, f)
                os.replace(tmp, stats_path)  # atomic: reporter never reads a partial file
            except OSError:
                pass
            time.sleep(0.5)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((a.host, a.port))
    srv.listen(512)
    threading.Thread(target=writer, daemon=True).start()
    print(f"{a.name} listening on {a.host}:{a.port}", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
