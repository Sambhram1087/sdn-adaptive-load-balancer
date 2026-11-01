"""Ryu app: TCP load balancer with static (round-robin) and adaptive (load-aware) modes.

Mode:   LB_MODE=static|adaptive ryu-manager controller/lb_controller.py      (default: config `mode`)
        (Ryu parses its own CLI before loading apps, so app options are passed by environment.)
Config: LB_CONFIG=<path>        (default: ../config/config.yaml)
Logs:   LB_LOG_PREFIX=<prefix>  (default: results/controller) -> <prefix>_stats.csv, <prefix>_events.csv
"""
import csv
import json
import os
import socket
import sys
import time

import yaml

try:
    from ryu.base import app_manager
    from ryu.controller import ofp_event
    from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
    from ryu.lib import hub
    from ryu.lib.packet import arp, ethernet, ether_types, ipv4, packet, tcp
    from ryu.ofproto import ofproto_v1_3
    HAVE_RYU = True
except ImportError:  # lets LBState be unit-tested without Ryu installed
    HAVE_RYU = False

UP, OVERLOADED, DOWN, RECOVERING = "UP", "OVERLOADED", "DOWN", "RECOVERING"
HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
ROOT = os.path.dirname(HERE)


# --------------------------------------------------------------------------- pure state / policy
class Server:
    def __init__(self, idx, c, now):
        self.idx, self.name, self.ip, self.mac = idx, c["name"], c["ip"], c["mac"]
        self.port = int(c["port"])
        self.status = UP
        self.conns = 0          # last reported active connections
        self.pending = 0        # flows assigned since last report (avoids herding between reports)
        self.cpu = 0.0
        self.svc_ms = 0.0
        self.util = 0.0         # link utilisation 0..1
        self.last_report = now
        self.over_count = 0
        self.link_up = True
        self.assigned = 0
        self.score = 0.0
        self.prev = None        # (t, rx_bytes, tx_bytes)


class LBState:
    def __init__(self, cfg, mode="adaptive", now=None):
        now = time.time() if now is None else now
        self.mode = mode
        mon, th, w = cfg["monitoring"], cfg["thresholds"], cfg["weights"]
        norm = cfg.get("normalization", {})
        self.failure_timeout = float(mon["failure_timeout"])
        self.overload, self.recovery = float(th["overload"]), float(th["recovery"])
        self.consec = int(th["consecutive_intervals"])
        self.w_conn, self.w_cpu, self.w_link = (float(w["connections"]), float(w["cpu"]),
                                                float(w["link_utilization"]))
        self.max_conns = float(norm.get("max_connections", 10))
        self.capacity_bps = float(norm.get("link_capacity_mbps", 10)) * 1e6
        self.servers = [Server(i, s, now) for i, s in enumerate(cfg["servers"])]
        self.by_name = {s.name: s for s in self.servers}
        self.by_port = {s.port: s for s in self.servers}
        self.rr = 0

    # -- scoring
    def score(self, s):
        c = min(1.0, (s.conns + s.pending) / self.max_conns)
        cpu = min(1.0, s.cpu / 100.0)
        u = min(1.0, max(0.0, s.util))
        return self.w_conn * c + self.w_cpu * cpu + self.w_link * u

    def _ev(self, s, new, reason, now):
        old, s.status = s.status, new
        return {"ts": now, "server": s.name, "old": old, "new": new, "reason": reason,
                "score": round(self.score(s), 3)}

    # -- inputs
    def reset_clock(self, now):
        """Start the failure clock (called when the switch connects)."""
        for s in self.servers:
            s.last_report = now

    def update_report(self, name, d, now):
        s = self.by_name.get(name)
        if s is None:
            return []
        s.last_report = now
        s.conns, s.pending = int(d.get("conns", 0)), 0
        s.cpu, s.svc_ms = float(d.get("cpu", 0.0)), float(d.get("svc_ms", 0.0))
        if self.mode == "adaptive" and s.status == DOWN and s.link_up:
            return [self._ev(s, RECOVERING, "report-resumed", now)]
        return []

    def update_ports(self, port_no, rx, tx, now):
        s = self.by_port.get(port_no)
        if s is None:
            return
        if s.prev and now > s.prev[0]:
            dt = now - s.prev[0]
            rate = max(rx - s.prev[1], tx - s.prev[2]) * 8.0 / dt  # full duplex: busiest direction
            s.util = min(1.0, max(0.0, rate / self.capacity_bps))
        s.prev = (now, rx, tx)

    def set_link(self, port_no, up, now):
        s = self.by_port.get(port_no)
        if s is None:
            return []
        s.link_up = up
        if self.mode == "adaptive" and not up and s.status != DOWN:
            return [self._ev(s, DOWN, "port-down", now)]
        return []

    # -- periodic checks (adaptive only)
    def check_timeouts(self, now):
        if self.mode != "adaptive":
            return []
        return [self._ev(s, DOWN, "heartbeat-timeout", now) for s in self.servers
                if s.status != DOWN and now - s.last_report > self.failure_timeout]

    def evaluate(self, now):
        evs = []
        for s in self.servers:
            s.score = self.score(s)
            if self.mode != "adaptive" or s.status == DOWN:
                continue
            if s.status == UP:
                s.over_count = s.over_count + 1 if s.score > self.overload else 0
                if s.over_count >= self.consec:
                    s.over_count = 0
                    evs.append(self._ev(s, OVERLOADED, "score>overload", now))
            elif s.score < self.recovery:  # OVERLOADED or RECOVERING
                s.over_count = 0
                evs.append(self._ev(s, UP, "score<recovery", now))
        return evs

    # -- selection
    def select(self):
        n = len(self.servers)
        if self.mode == "static":  # plain round-robin: ignores load and failures
            s = self.servers[self.rr % n]
            self.rr += 1
        else:
            pool = []
            for wanted in (UP, OVERLOADED, RECOVERING):  # fall back only if no better tier exists
                pool = [s for s in self.servers if s.status == wanted]
                if pool:
                    break
            if not pool:
                return None
            order = [self.servers[(self.rr + i) % n] for i in range(n)]  # rotate for tie-breaking
            self.rr += 1
            s = None
            for c in order:
                if c in pool and (s is None or self.score(c) < self.score(s)):
                    s = c
            s.pending += 1
        s.assigned += 1
        return s


def resolve_mode(cfg):
    mode = os.environ.get("LB_MODE")
    if not mode:
        for i, a in enumerate(sys.argv):
            if a == "--mode" and i + 1 < len(sys.argv):
                mode = sys.argv[i + 1]
            elif a.startswith("--mode="):
                mode = a.split("=", 1)[1]
    mode = (mode or cfg.get("mode", "adaptive")).lower()
    if mode not in ("static", "adaptive"):
        raise ValueError(f"invalid mode {mode!r}; use static or adaptive")
    return mode


class _Csv:
    def __init__(self, path, header):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        self.f = open(path, "a", newline="")
        self.w = csv.writer(self.f)
        if new:
            self.w.writerow(header)
            self.f.flush()

    def row(self, r):
        self.w.writerow(r)
        self.f.flush()


# --------------------------------------------------------------------------- Ryu app
if HAVE_RYU:
    class LBController(app_manager.RyuApp):
        OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            cfg_path = os.environ.get("LB_CONFIG", os.path.join(ROOT, "config", "config.yaml"))
            self.cfg = yaml.safe_load(open(cfg_path))
            self.mode = resolve_mode(self.cfg)
            self.state = LBState(self.cfg, self.mode)
            c = self.cfg
            self.vip, self.vip_mac, self.svc_port = c["vip"], c["vip_mac"], int(c["service_port"])
            self.idle, self.hard = int(c["flow"]["idle_timeout"]), int(c["flow"]["hard_timeout"])
            self.stats_interval = float(c["monitoring"]["stats_interval"])
            self.report_port = int(c["monitoring"]["report_port"])
            self.dp = None
            self.mac_to_port = {}
            self.flow_map = {}       # (client ip, client port) -> (server idx, last seen)
            self.packet_in = 0
            prefix = os.environ.get("LB_LOG_PREFIX", os.path.join(ROOT, "results", "controller"))
            names = [s.name for s in self.state.servers]
            hdr = ["ts", "mode", "packet_in_total"] + [f"{n}_{k}" for n in names
                   for k in ("assigned", "conns", "cpu", "util", "score", "status")]
            self.stats_log = _Csv(prefix + "_stats.csv", hdr)
            self.event_log = _Csv(prefix + "_events.csv",
                                  ["ts", "mode", "server", "old", "new", "reason", "score"])
            self.logger.info("LB controller started: mode=%s config=%s", self.mode, cfg_path)
            hub.spawn(self._report_listener)
            hub.spawn(self._poll_loop)
            hub.spawn(self._failure_loop)

        # ---- helpers
        def _add_flow(self, dp, prio, match, actions, idle=0, hard=0, cookie=0):
            p = dp.ofproto_parser
            inst = [p.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)]
            dp.send_msg(p.OFPFlowMod(datapath=dp, priority=prio, match=match, instructions=inst,
                                     idle_timeout=idle, hard_timeout=hard, cookie=cookie))

        def _packet_out(self, dp, actions, data, in_port):
            o, p = dp.ofproto, dp.ofproto_parser
            dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=o.OFP_NO_BUFFER, in_port=in_port,
                                       actions=actions, data=data))

        def _delete_flows(self, dp, cookie=0, mask=0):
            o, p = dp.ofproto, dp.ofproto_parser
            dp.send_msg(p.OFPFlowMod(datapath=dp, command=o.OFPFC_DELETE, table_id=o.OFPTT_ALL,
                                     out_port=o.OFPP_ANY, out_group=o.OFPG_ANY,
                                     cookie=cookie, cookie_mask=mask, match=p.OFPMatch()))

        def _log_events(self, events):
            for e in events:
                self.logger.info("server %s: %s -> %s (%s, score=%.2f)",
                                 e["server"], e["old"], e["new"], e["reason"], e["score"])
                self.event_log.row([f"{e['ts']:.3f}", self.mode, e["server"], e["old"], e["new"],
                                    e["reason"], e["score"]])
                if e["new"] == DOWN and self.dp is not None:
                    self._flush_server(self.state.by_name[e["server"]])

        def _flush_server(self, s):
            """Delete every flow pointing to / coming from a failed server (cookie = idx+1)."""
            self._delete_flows(self.dp, cookie=s.idx + 1, mask=0xFFFFFFFFFFFFFFFF)
            self.flow_map = {k: v for k, v in self.flow_map.items() if v[0] != s.idx}

        # ---- switch handshake / port events
        @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
        def _features(self, ev):
            dp = ev.msg.datapath
            o, p = dp.ofproto, dp.ofproto_parser
            self.dp = dp
            self._delete_flows(dp)  # clear stale state
            self._add_flow(dp, 0, p.OFPMatch(),
                           [p.OFPActionOutput(o.OFPP_CONTROLLER, o.OFPCML_NO_BUFFER)])
            self.state.reset_clock(time.time())

        @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
        def _port_status(self, ev):
            o = ev.msg.datapath.ofproto
            d = ev.msg.desc
            down = bool(d.state & o.OFPPS_LINK_DOWN) or bool(d.config & o.OFPPC_PORT_DOWN) \
                or ev.msg.reason == o.OFPPR_DELETE
            self._log_events(self.state.set_link(d.port_no, not down, time.time()))

        # ---- packet in
        @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
        def _packet_in(self, ev):
            msg = ev.msg
            dp = msg.datapath
            in_port = msg.match["in_port"]
            pkt = packet.Packet(msg.data)
            eth = pkt.get_protocol(ethernet.ethernet)
            if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
                return
            self.packet_in += 1
            self.mac_to_port[eth.src] = in_port
            a = pkt.get_protocol(arp.arp)
            if a and a.opcode == arp.ARP_REQUEST and a.dst_ip == self.vip:
                return self._arp_reply(dp, in_port, eth, a)
            ip, t = pkt.get_protocol(ipv4.ipv4), pkt.get_protocol(tcp.tcp)
            if ip and t and ip.dst == self.vip and t.dst_port == self.svc_port:
                return self._balance(dp, msg, in_port, eth, ip, t)
            self._l2(dp, msg, in_port, eth)

        def _arp_reply(self, dp, port, eth, a):
            e = ethernet.ethernet(dst=eth.src, src=self.vip_mac, ethertype=ether_types.ETH_TYPE_ARP)
            r = arp.arp(opcode=arp.ARP_REPLY, src_mac=self.vip_mac, src_ip=self.vip,
                        dst_mac=a.src_mac, dst_ip=a.src_ip)
            pk = packet.Packet()
            pk.add_protocol(e)
            pk.add_protocol(r)
            pk.serialize()
            self._packet_out(dp, [dp.ofproto_parser.OFPActionOutput(port)], pk.data,
                             dp.ofproto.OFPP_CONTROLLER)

        def _l2(self, dp, msg, in_port, eth):
            o, p = dp.ofproto, dp.ofproto_parser
            out = self.mac_to_port.get(eth.dst)
            actions = [p.OFPActionOutput(out if out is not None else o.OFPP_FLOOD)]
            if out is not None:
                self._add_flow(dp, 1, p.OFPMatch(in_port=in_port, eth_dst=eth.dst), actions, idle=30)
            self._packet_out(dp, actions, msg.data, in_port)

        def _balance(self, dp, msg, in_port, eth, ip, t):
            p = dp.ofproto_parser
            key = (ip.src, t.src_port)
            is_syn = bool(t.bits & tcp.TCP_SYN) and not (t.bits & tcp.TCP_ACK)
            srv = None
            if not is_syn and key in self.flow_map:  # mid-connection packet after flow expiry
                cand = self.state.servers[self.flow_map[key][0]]
                if self.mode == "static" or cand.status != DOWN:
                    srv = cand
            if srv is None:
                srv = self.state.select()
            if srv is None:
                self.logger.warning("no healthy server; dropping %s:%s", *key)
                return
            self.flow_map[key] = (srv.idx, time.time())
            cookie = srv.idx + 1
            fwd = p.OFPMatch(in_port=in_port, eth_type=0x0800, ip_proto=6, ipv4_src=ip.src,
                             ipv4_dst=self.vip, tcp_src=t.src_port, tcp_dst=t.dst_port)
            fwd_act = [p.OFPActionSetField(eth_dst=srv.mac), p.OFPActionSetField(ipv4_dst=srv.ip),
                       p.OFPActionOutput(srv.port)]
            rev = p.OFPMatch(in_port=srv.port, eth_type=0x0800, ip_proto=6, ipv4_src=srv.ip,
                             ipv4_dst=ip.src, tcp_src=t.dst_port, tcp_dst=t.src_port)
            rev_act = [p.OFPActionSetField(eth_src=self.vip_mac), p.OFPActionSetField(eth_dst=eth.src),
                       p.OFPActionSetField(ipv4_src=self.vip), p.OFPActionOutput(in_port)]
            self._add_flow(dp, 10, fwd, fwd_act, self.idle, self.hard, cookie)
            self._add_flow(dp, 10, rev, rev_act, self.idle, self.hard, cookie)
            self._packet_out(dp, fwd_act, msg.data, in_port)

        # ---- background tasks
        def _report_listener(self):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", self.report_port))
            except OSError as e:
                self.logger.error("cannot bind UDP %d: %s", self.report_port, e)
                return
            self.logger.info("listening for load reports on UDP %d", self.report_port)
            while True:
                data, _ = sock.recvfrom(4096)
                try:
                    d = json.loads(data.decode())
                    self._log_events(self.state.update_report(d.get("name"), d, time.time()))
                except (ValueError, TypeError, KeyError):
                    self.logger.debug("bad report: %r", data)

        def _poll_loop(self):
            while True:
                hub.sleep(self.stats_interval)
                if self.dp is not None:
                    o, p = self.dp.ofproto, self.dp.ofproto_parser
                    self.dp.send_msg(p.OFPPortStatsRequest(self.dp, 0, o.OFPP_ANY))

        def _failure_loop(self):
            while True:
                hub.sleep(0.5)
                self._log_events(self.state.check_timeouts(time.time()))

        @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
        def _port_stats(self, ev):
            now = time.time()
            for st in ev.msg.body:
                self.state.update_ports(st.port_no, st.rx_bytes, st.tx_bytes, now)
            self._log_events(self.state.evaluate(now))
            row = [f"{now:.3f}", self.mode, self.packet_in]
            for s in self.state.servers:
                row += [s.assigned, s.conns, f"{s.cpu:.1f}", f"{s.util:.3f}", f"{s.score:.3f}", s.status]
            self.stats_log.row(row)
            horizon = now - 2 * self.hard
            self.flow_map = {k: v for k, v in self.flow_map.items() if v[1] > horizon}
