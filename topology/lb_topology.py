#!/usr/bin/env python3
"""Mininet topology: 3 clients (h1-h3), 3 servers (h4-h6), 1 OVS switch (s1), remote Ryu controller.

Server links are attached first so switch ports match config.yaml (h4->1, h5->2, h6->3); clients use
ports 4-6. A root-namespace interface (10.0.0.254) is attached as port 7 so servers can send UDP load
reports to the controller. Servers and reporters are started automatically.

Usage: sudo python3 topology/lb_topology.py [--no-cli] [--bw 10] [--controller-ip 127.0.0.1]
Host PIDs are written to /tmp/lb_hosts/<host>.pid (tests use `mnexec -a`); /tmp/lb_hosts/ready marks readiness.
"""
import argparse
import os
import signal
import subprocess
import sys
import threading

import yaml
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.util import connectToRootNS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PID_DIR = "/tmp/lb_hosts"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(ROOT, "config", "config.yaml"))
    ap.add_argument("--controller-ip", default="127.0.0.1")
    ap.add_argument("--controller-port", type=int, default=6653)
    ap.add_argument("--bw", type=float, default=10, help="link bandwidth in Mbit/s (match config normalization)")
    ap.add_argument("--no-cli", action="store_true", help="run until SIGINT/SIGTERM instead of opening the CLI")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config))
    setLogLevel("info")

    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=False, waitConnected=False)
    net.addController("c0", controller=RemoteController, ip=a.controller_ip, port=a.controller_port)
    s1 = net.addSwitch("s1", protocols="OpenFlow13")

    for srv in cfg["servers"]:  # servers first => switch ports 1..3
        h = net.addHost("h%s" % srv["ip"].split(".")[-1], ip=srv["ip"] + "/24", mac=srv["mac"])
        net.addLink(h, s1, bw=a.bw)
    for i in (1, 2, 3):
        h = net.addHost("h%d" % i, ip="10.0.0.%d/24" % i, mac="00:00:00:00:00:%02d" % i)
        net.addLink(h, s1, bw=a.bw)

    ctrl_ip = cfg["monitoring"]["controller_ip"]
    connectToRootNS(net, s1, ctrl_ip + "/24", [])  # NOTE: this also starts the network
    if not net.waitConnected(timeout=30):
        info("*** WARNING: switch did not connect to the controller; is ryu-manager running?\n")

    logdir = os.path.join(ROOT, "results", "logs")
    os.makedirs(logdir, exist_ok=True)
    procs = []
    port = str(cfg["service_port"])
    for srv in cfg["servers"]:
        h = net.get("h%s" % srv["ip"].split(".")[-1])
        for script, extra, tag in (("servers/tcp_server.py", ["--port", port], "server"),
                                   ("monitoring/server_reporter.py", ["--config", a.config], "reporter")):
            log = open(os.path.join(logdir, "%s_%s.log" % (srv["name"], tag)), "w")
            procs.append(h.popen(["python3", os.path.join(ROOT, script), "--name", srv["name"]] + extra,
                                 stdout=log, stderr=subprocess.STDOUT))
        info("*** started %s (%s) on %s\n" % (srv["name"], srv["ip"], h.name))

    os.makedirs(PID_DIR, exist_ok=True)
    for h in net.hosts:
        with open(os.path.join(PID_DIR, h.name + ".pid"), "w") as f:
            f.write(str(h.pid))
    open(os.path.join(PID_DIR, "ready"), "w").close()
    info("*** topology ready (VIP %s:%s)\n" % (cfg["vip"], port))

    try:
        if a.no_cli:
            stop = threading.Event()
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: stop.set())
            stop.wait()
        else:
            CLI(net)
    finally:
        for p in procs:
            p.terminate()
        for f in os.listdir(PID_DIR) if os.path.isdir(PID_DIR) else []:
            os.remove(os.path.join(PID_DIR, f))
        net.stop()


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("run with sudo")
    main()
