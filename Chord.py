# chord.py  (updated with local storage methods for DFS)
from __future__ import annotations
import argparse
import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
import Pyro5.api as pyro
from Pyro5.server import expose, oneway


# ----------------------------
# Utilities
# ----------------------------

def sha1_int(s: str) -> int:
    return int(hashlib.sha1(s.encode("utf-8")).hexdigest(), 16)

def in_interval(x, a, b, mod, left_open=True, right_closed=True):
    if a == b:
        return True
    def _in_linear(xv, av, bv):
        left_ok = (xv > av) if left_open else (xv >= av)
        right_ok = (xv <= bv) if right_closed else (xv < bv)
        return left_ok and right_ok
    if a < b:
        return _in_linear(x, a, b)
    else:
        return _in_linear(x, a, mod - 1) or _in_linear(x, 0, b)

@dataclass(frozen=True)
class NodeInfo:
    node_id: int
    host: str
    port: int

    @property
    def name(self):
        return f"chord.node.{self.node_id}"

    @property
    def addr(self):
        return f"{self.host}:{self.port}"

def node_id_for(host, port, m):
    return sha1_int(f"{host}:{port}") % (2 ** m)

def proxy_for(node_id):
    return pyro.Proxy(f"PYRONAME:chord.node.{node_id}")


# ----------------------------
# Chord Node
# ----------------------------

@expose
class ChordNode:
    def __init__(self, info: NodeInfo, m: int) -> None:
        self.info = info
        self.m = m
        self.ring_size = 2 ** m
        self._lock = threading.RLock()
        self.successor: NodeInfo = info
        self.predecessor: Optional[NodeInfo] = None
        self.fingers: List[NodeInfo] = [info for _ in range(m)]
        self._next_finger = 0
        self._stop = threading.Event()

        # ── Local key-value store (used by DFS) ──
        self._store: Dict[str, str] = {}
        self._store_lock = threading.Lock()

    # ---------- Storage methods (called by DFS via Pyro) ----------

    def store_put(self, key: str, value: str) -> None:
        with self._store_lock:
            self._store[key] = value

    def store_get(self, key: str) -> Optional[str]:
        with self._store_lock:
            return self._store.get(key, None)

    def store_delete(self, key: str) -> None:
        with self._store_lock:
            self._store.pop(key, None)

    def store_keys(self) -> List[str]:
        with self._store_lock:
            return list(self._store.keys())

    # ---------- Exposed Chord helpers ----------

    def ping(self) -> str:
        return "pong"

    def get_node_info(self) -> Dict[str, Any]:
        return {"node_id": self.info.node_id, "host": self.info.host, "port": self.info.port}

    def get_successor(self) -> Dict[str, Any]:
        with self._lock:
            s = self.successor
            return {"node_id": s.node_id, "host": s.host, "port": s.port}

    def get_predecessor(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            p = self.predecessor
            if p is None:
                return None
            return {"node_id": p.node_id, "host": p.host, "port": p.port}

    def set_successor(self, node: Dict[str, Any]) -> None:
        with self._lock:
            self.successor = NodeInfo(node["node_id"], node["host"], node["port"])

    def set_predecessor(self, node: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            if node is None:
                self.predecessor = None
            else:
                self.predecessor = NodeInfo(node["node_id"], node["host"], node["port"])

    def find_successor(self, key: int) -> Dict[str, Any]:
        with self._lock:
            n = self.info
            s = self.successor
            if in_interval(key, n.node_id, s.node_id, self.ring_size,
                           left_open=True, right_closed=True):
                return {"node_id": s.node_id, "host": s.host, "port": s.port}
            cp = self._closest_preceding_finger_local(key)
            if cp.node_id == n.node_id:
                cp = s
        with proxy_for(cp.node_id) as p:
            return p.find_successor(key)

    def closest_preceding_finger(self, key: int) -> Dict[str, Any]:
        cp = self._closest_preceding_finger_local(key)
        return {"node_id": cp.node_id, "host": cp.host, "port": cp.port}

    def notify(self, node: Dict[str, Any]) -> None:
        cand = NodeInfo(node["node_id"], node["host"], node["port"])
        with self._lock:
            if self.predecessor is None:
                self.predecessor = cand
                return
            p = self.predecessor
            if in_interval(cand.node_id, p.node_id, self.info.node_id,
                           self.ring_size, left_open=True, right_closed=False):
                self.predecessor = cand

    def _closest_preceding_finger_local(self, key: int) -> NodeInfo:
        with self._lock:
            for i in reversed(range(self.m)):
                f = self.fingers[i]
                if in_interval(f.node_id, self.info.node_id, key, self.ring_size,
                               left_open=True, right_closed=False):
                    return f
            return self.info

    # ---------- Maintenance ----------

    @oneway
    def start_maintenance(self, stabilize_period=1.0, fix_fingers_period=1.0):
        threading.Thread(target=self._stabilize_loop, args=(stabilize_period,), daemon=True).start()
        threading.Thread(target=self._fix_fingers_loop, args=(fix_fingers_period,), daemon=True).start()

    @oneway
    def stop(self):
        self._stop.set()

    def _stabilize_loop(self, period):
        while not self._stop.is_set():
            try:
                self.stabilize()
            except Exception:
                pass
            time.sleep(period)

    def _fix_fingers_loop(self, period):
        while not self._stop.is_set():
            try:
                self.fix_fingers()
            except Exception:
                pass
            time.sleep(period)

    def stabilize(self):
        with self._lock:
            s = self.successor
            n = self.info
        try:
            with proxy_for(s.node_id) as ps:
                x_dict = ps.get_predecessor()
        except Exception:
            return
        if x_dict is not None:
            x = NodeInfo(x_dict["node_id"], x_dict["host"], x_dict["port"])
            if in_interval(x.node_id, n.node_id, s.node_id, self.ring_size,
                           left_open=True, right_closed=False):
                with self._lock:
                    self.successor = x
                    self.fingers[0] = x
                s = x
        try:
            with proxy_for(s.node_id) as ps:
                ps.notify({"node_id": n.node_id, "host": n.host, "port": n.port})
        except Exception:
            return

    def fix_fingers(self):
        with self._lock:
            i = self._next_finger
            self._next_finger = (self._next_finger + 1) % self.m
            start = (self.info.node_id + (1 << i)) % self.ring_size
        succ_dict = self.find_successor(start)
        succ = NodeInfo(succ_dict["node_id"], succ_dict["host"], succ_dict["port"])
        with self._lock:
            self.fingers[i] = succ
            if i == 0:
                self.successor = succ

    # ---------- Ring operations ----------

    def create(self):
        with self._lock:
            self.predecessor = None
            self.successor = self.info
            self.fingers = [self.info for _ in range(self.m)]

    def join(self, known: Dict[str, Any]):
        known_id = known["node_id"]
        with proxy_for(known_id) as pk:
            succ_dict = pk.find_successor(self.info.node_id)
        succ = NodeInfo(succ_dict["node_id"], succ_dict["host"], succ_dict["port"])
        with self._lock:
            self.predecessor = None
            self.successor = succ
            self.fingers[0] = succ


# ----------------------------
# Process runner
# ----------------------------

def run_node(host, port, m, bootstrap, join_addr):
    nid = node_id_for(host, port, m)
    info = NodeInfo(nid, host, port)
    node = ChordNode(info, m)
    ns = pyro.locate_ns()
    with pyro.Daemon(host=host, port=port) as daemon:
        uri = daemon.register(node)
        ns.register(info.name, uri)
        print(f"[node {nid}] registered {info.name} -> {uri}")
        if bootstrap:
            node.create()
            print(f"[node {nid}] created new ring")
        else:
            if not join_addr:
                raise SystemExit("Must supply --join host:port")
            jh, jp = join_addr.split(":")
            jp_i = int(jp)
            jid = node_id_for(jh, jp_i, m)
            node.join({"node_id": jid, "host": jh, "port": jp_i})
            print(f"[node {nid}] joined via {jid}; successor={node.successor.node_id}")
        node.start_maintenance()
        print(f"[node {nid}] running; ctrl-c to stop")
        daemon.requestLoop()

def run_lookup(start_addr, m, key):
    sh, sp = start_addr.split(":")
    sid = node_id_for(sh, int(sp), m)
    with proxy_for(sid) as p:
        res = p.find_successor(key)
    print(f"[lookup] key={key} -> node {res['node_id']} ({res['host']}:{res['port']})")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_node = sub.add_parser("node")
    ap_node.add_argument("--host", default="127.0.0.1")
    ap_node.add_argument("--port", type=int, required=True)
    ap_node.add_argument("--m", type=int, default=8)
    ap_node.add_argument("--bootstrap", action="store_true")
    ap_node.add_argument("--join", dest="join_addr", default=None)

    ap_lookup = sub.add_parser("lookup")
    ap_lookup.add_argument("--start", required=True)
    ap_lookup.add_argument("--m", type=int, default=8)
    ap_lookup.add_argument("--key", type=int, required=True)

    args = ap.parse_args()
    if args.cmd == "node":
        run_node(args.host, args.port, args.m, args.bootstrap, args.join_addr)
    else:
        run_lookup(args.start, args.m, args.key)

if __name__ == "__main__":
    main()