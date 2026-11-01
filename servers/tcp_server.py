#!/usr/bin/env python3
"""Multi-threaded TCP server. Protocol: client sends "REQ <id>\n", server replies "OK <name> <id> <ms>\n".

Runtime throttling (for overload tests): write "<delay_ms> [work_ms]" to /tmp/lb_<name>.ctl.
Load stats are published to /tmp/lb_<name>.stats for monitoring/server_reporter.py.
"""
import argparse, json, os, signal, socket, socketserver, threading, time
from collections import deque


def spin(ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        pass


class Ctx:
    def __init__(self, a):
        self.name, self.dir = a.name, a.stats_dir
        self.delay, self.work = a.delay_ms, a.work_ms
        self.idle = a.idle_timeout
        self.lock = threading.Lock()
        self.active = 0
        self.total = 0
        self.recent = deque(maxlen=200)
        self.ctl_path = os.path.join(self.dir, f"lb_{self.name}.ctl")
        self.stats_path = os.path.join(self.dir, f"lb_{self.name}.stats")
        self._ctl_t = 0.0
        self._ctl = (self.delay, self.work)

    def control(self):
        now = time.time()
        if now - self._ctl_t > 0.5:
            self._ctl_t = now
            try:
                p = open(self.ctl_path).read().split()
                self._ctl = (float(p[0]), float(p[1]) if len(p) > 1 else self.work)
            except (OSError, ValueError, IndexError):
                self._ctl = (self.delay, self.work)
        return self._ctl

    def enter(self):
        with self.lock:
            self.active += 1

    def leave(self):
        with self.lock:
            self.active -= 1

    def record(self, ms):
        with self.lock:
            self.total += 1
            self.recent.append(ms)

    def publish(self):
        with self.lock:
            avg = sum(self.recent) / len(self.recent) if self.recent else 0.0
            d = {"pid": os.getpid(), "ts": time.time(), "active": self.active,
                 "total": self.total, "avg_ms": round(avg, 2)}
        tmp = self.stats_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, self.stats_path)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        c = self.server.ctx
        c.enter()
        try:
            self.request.settimeout(c.idle)
            for line in self.rfile:
                t0 = time.perf_counter()
                parts = line.decode(errors="replace").split()
                if not parts:
                    continue
                if parts[0] == "QUIT":
                    break
                rid = parts[1] if len(parts) > 1 else "0"
                delay, work = c.control()
                spin(work)
                time.sleep(delay / 1000.0)
                ms = (time.perf_counter() - t0) * 1000.0
                c.record(ms)
                self.wfile.write(f"OK {c.name} {rid} {ms:.2f}\n".encode())
        except (socket.timeout, OSError):
            pass
        finally:
            c.leave()


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 256


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", required=True)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--delay-ms", type=float, default=20.0, help="sleep per request")
    ap.add_argument("--work-ms", type=float, default=5.0, help="CPU busy-loop per request")
    ap.add_argument("--idle-timeout", type=float, default=10.0)
    ap.add_argument("--stats-dir", default="/tmp")
    a = ap.parse_args()
    ctx = Ctx(a)
    srv = Server((a.host, a.port), Handler)
    srv.ctx = ctx

    def publisher():
        while True:
            try:
                ctx.publish()
            except OSError:
                pass
            time.sleep(0.5)

    threading.Thread(target=publisher, daemon=True).start()

    def stop(*_):
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"[{a.name}] listening on {a.host}:{a.port}", flush=True)
    try:
        srv.serve_forever()
    finally:
        try:
            os.remove(ctx.stats_path)
        except OSError:
            pass
        srv.server_close()


if __name__ == "__main__":
    main()
