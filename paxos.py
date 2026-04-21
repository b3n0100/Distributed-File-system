from __future__ import annotations
import argparse
import json
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import Pyro5.api as pyro
from Pyro5.server import expose


@expose
class PaxosReplica:
    def __init__(self, replica_id: int):
        self.replica_id = replica_id
        self._lock = threading.RLock()
        self.accepted: Dict[int, Dict[str, Any]] = {}
        self.committed: Dict[int, Dict[str, Any]] = {}

    def ping(self) -> str:
        return "pong"

    def accept(self, ballot: int, slot: int, op: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            current = self.accepted.get(slot)
            if current is None or ballot >= current["ballot"]:
                self.accepted[slot] = {"ballot": ballot, "op": op}
                print(f"[replica {self.replica_id}] ACCEPT slot={slot} ballot={ballot} op={op['type']} key={op['key']}")
                return {"ok": True, "replica_id": self.replica_id, "slot": slot, "ballot": ballot}
            return {"ok": False, "replica_id": self.replica_id, "slot": slot, "ballot": current['ballot']}

    def learn(self, ballot: int, slot: int, op: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            current = self.accepted.get(slot)
            if current is None or current["ballot"] != ballot:
                return {"ok": False, "replica_id": self.replica_id, "reason": "ballot mismatch"}
            self.committed[slot] = {"ballot": ballot, "op": op}
            print(f"[replica {self.replica_id}] LEARN slot={slot} ballot={ballot} op={op['type']} key={op['key']}")
            return {"ok": True, "replica_id": self.replica_id, "slot": slot, "ballot": ballot}

    def dump_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "replica_id": self.replica_id,
                "accepted": self.accepted,
                "committed": self.committed,
            }


def run_replica(host: str, port: int, replica_id: int) -> None:
    name = f"paxos.replica.{replica_id}"
    ns = pyro.locate_ns()
    replica = PaxosReplica(replica_id)
    with pyro.Daemon(host=host, port=port) as daemon:
        uri = daemon.register(replica)
        ns.register(name, uri)
        print(f"[replica {replica_id}] registered {name} -> {uri}")
        daemon.requestLoop()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--replica-id", type=int, required=True)
    args = ap.parse_args()
    run_replica(args.host, args.port, args.replica_id)


if __name__ == "__main__":
    main()
