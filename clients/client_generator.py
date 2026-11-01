#!/usr/bin/env python3
"""Concurrent TCP client workload. Each client thread loops: connect to VIP, send REQ, read reply, close."""
import argparse, csv, json, math, random, socket, threading, time


def pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, math.ceil(p / 100.0 * len(sorted_vals)) - 1))
    return sorted_vals[k]


def worker(cid, a, stop_at, out):
    rid = 0
    time.sleep(random.uniform(0, min(1.0, a.think * 5)))  # de-synchronise start
    while time.time() < stop_at:
        rid += 1
        t0 = time.time()
        server, status, s = "-", "ok", None
        try:
            s = socket.create_connection((a.vip, a.port), timeout=a.timeout)
            s.settimeout(a.timeout)
            s.sendall(f"REQ {cid}-{rid}\n".encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(1024)
                if not chunk:
                    raise ConnectionError("closed")
                buf += chunk
            parts = buf.decode(errors="replace").split()
            if parts and parts[0] == "OK" and len(parts) > 1:
                server = parts[1]
            else:
                status = "bad_reply"
        except socket.timeout:
            status = "timeout"
        except OSError:
            status = "error"
        finally:
            if s:
                try:
                    s.close()
                except OSError:
                    pass
        out.append((t0, cid, rid, server, (time.time() - t0) * 1000.0, status))
        if a.think > 0:
            time.sleep(random.uniform(0.5, 1.5) * a.think)


def summarize(rows, elapsed, label):
    ok = [r for r in rows if r[5] == "ok"]
    lat = sorted(r[4] for r in ok)
    per = {}
    for r in ok:
        per[r[3]] = per.get(r[3], 0) + 1
    total = len(rows)
    return {"label": label, "total": total, "ok": len(ok), "failed": total - len(ok),
            "fail_pct": round(100.0 * (total - len(ok)) / total, 2) if total else 0.0,
            "avg_ms": round(sum(lat) / len(lat), 2) if lat else 0.0,
            "p50_ms": round(pct(lat, 50), 2), "p95_ms": round(pct(lat, 95), 2),
            "p99_ms": round(pct(lat, 99), 2),
            "throughput_rps": round(len(ok) / elapsed, 2) if elapsed > 0 else 0.0,
            "per_server": per, "duration_s": round(elapsed, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vip", default="10.0.0.100")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--clients", type=int, default=10)
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--think", type=float, default=0.1, help="mean think time between requests (s)")
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--csv", help="write per-request log here")
    ap.add_argument("--summary", help="write summary JSON here")
    ap.add_argument("--label", default="")
    a = ap.parse_args()

    start = time.time()
    stop_at = start + a.duration
    outs = [[] for _ in range(a.clients)]
    ths = [threading.Thread(target=worker, args=(i, a, stop_at, outs[i]), daemon=True) for i in range(a.clients)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    rows = sorted((r for o in outs for r in o), key=lambda r: r[0])
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts", "client", "req", "server", "latency_ms", "status"])
            for r in rows:
                w.writerow([f"{r[0]:.4f}", r[1], r[2], r[3], f"{r[4]:.2f}", r[5]])
    s = summarize(rows, time.time() - start, a.label)
    if a.summary:
        with open(a.summary, "w") as f:
            json.dump(s, f)
    print(json.dumps(s))


if __name__ == "__main__":
    main()
