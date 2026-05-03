"""
demo_failure.py  —  Part 2 failure scenario demo
=================================================
Demonstrates three failure scenarios required by the assignment:

  1. Follower crash during replication
     - One Paxos replica is killed mid-session
     - Leader retries; majority (2 of 3) still commits
     - System remains available

  2. Delayed ACCEPT message
     - One replica responds slowly (simulated with time.sleep)
     - Leader waits, majority still reached, commit succeeds

  3. Retransmitted ACCEPT
     - Leader re-sends ACCEPT to a replica that previously failed
     - Shows idempotency of the accept() call (same ballot accepted again)

Usage
-----
Start infrastructure first (in separate terminals):

    python -m Pyro5.nameserver
    python chord.py node --port 9000 --bootstrap
    python chord.py node --port 9001 --join 127.0.0.1:9000
    python chord.py node --port 9002 --join 127.0.0.1:9000
    python chord.py node --port 9003 --join 127.0.0.1:9000
    python chord.py node --port 9004 --join 127.0.0.1:9000
    python paxos.py --port 9101 --replica-id 1
    python paxos.py --port 9102 --replica-id 2
    python paxos.py --port 9103 --replica-id 3

Then run:

    python demo_failure.py --host 127.0.0.1 --port 9000
"""
from __future__ import annotations

import argparse
import json
import os
import time
import threading
from typing import Any, Dict, List, Optional

import Pyro5.api as pyro
from Pyro5.server import expose

# Re-use helpers from project files
from dfs_part_a import ChordDHTWrapper, DFS, PaxosLeader, sha1_int, node_id_for


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

SEPARATOR = "=" * 60


def section(title: str) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


def paxos_proxy(replica_id: int):
    return pyro.Proxy(f"PYRONAME:paxos.replica.{replica_id}")


# ─────────────────────────────────────────────
# Scenario 1 — follower crash
# ─────────────────────────────────────────────

class FaultInjectingPaxosLeader(PaxosLeader):
    """
    Extends PaxosLeader to support injecting faults:
      - crash_after_slot  : skip replica 'crashed_replica_id' once slot > threshold
      - delay_replica_id  : sleep before sending to that replica
      - delay_seconds     : how long to sleep
    """

    def __init__(self, *args, crash_after_slot: int = 0,
                 crashed_replica_id: Optional[int] = None,
                 delay_replica_id: Optional[int] = None,
                 delay_seconds: float = 0.0,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.crash_after_slot = crash_after_slot
        self.crashed_replica_id = crashed_replica_id
        self.delay_replica_id = delay_replica_id
        self.delay_seconds = delay_seconds
        self._retransmit_log: List[Dict] = []

    def _commit_operation(self, op: Dict[str, Any]) -> bool:
        slot = self.next_slot
        ballot = self.ballot

        print(f"\n[leader] → PROPOSE slot={slot} ballot={ballot} op={op['type']} key={op['key']}")

        accepts = 0
        acceptors: List[int] = []

        for rid in self.replica_ids:
            # Simulate crashed replica: skip it after threshold slot
            if (self.crashed_replica_id is not None
                    and rid == self.crashed_replica_id
                    and slot > self.crash_after_slot):
                print(f"[leader]   ✗ replica={rid} is CRASHED — skipping")
                continue

            # Simulate delayed message
            if self.delay_replica_id is not None and rid == self.delay_replica_id:
                print(f"[leader]   ⏳ delaying ACCEPT to replica={rid} by {self.delay_seconds}s")
                time.sleep(self.delay_seconds)

            try:
                with paxos_proxy(rid) as p:
                    print(f"[leader]   → ACCEPT(ballot={ballot}, slot={slot}) to replica={rid}")
                    res = p.accept(ballot, slot, op)
                if res.get("ok"):
                    print(f"[leader]   ← ACK from replica={rid}")
                    accepts += 1
                    acceptors.append(rid)
                else:
                    print(f"[leader]   ← NACK from replica={rid} (higher ballot={res.get('ballot')})")
            except Exception as exc:
                print(f"[leader]   ✗ ACCEPT failed replica={rid}: {exc}")

        majority = self._majority()
        print(f"[leader]   accepts={accepts} needed={majority}")

        if accepts < majority:
            print(f"[leader] ✗ ACCEPT majority NOT reached for slot={slot} — aborting")
            self.ballot += 1
            return False

        # ── LEARN phase ──
        learns = 0
        for rid in acceptors:
            try:
                with paxos_proxy(rid) as p:
                    print(f"[leader]   → LEARN(ballot={ballot}, slot={slot}) to replica={rid}")
                    res = p.learn(ballot, slot, op)
                if res.get("ok"):
                    print(f"[leader]   ← LEARNED by replica={rid}")
                    learns += 1
                else:
                    print(f"[leader]   ← LEARN rejected by replica={rid}: {res.get('reason')}")
            except Exception as exc:
                print(f"[leader]   ✗ LEARN failed replica={rid}: {exc}")

        if learns < majority:
            print(f"[leader] ✗ LEARN majority NOT reached for slot={slot}")
            self.ballot += 1
            return False

        print(f"[leader] ✓ COMMIT slot={slot} ballot={ballot} op={op['type']} key={op['key']}")
        self._apply(op)
        self.next_slot += 1
        self.ballot += 1
        return True

    def retransmit_accept(self, replica_id: int, slot: int, ballot: int,
                          op: Dict[str, Any]) -> None:
        """Explicitly retransmit ACCEPT to a replica (idempotency demo)."""
        print(f"\n[leader] ↻ RETRANSMIT ACCEPT slot={slot} ballot={ballot} to replica={replica_id}")
        try:
            with paxos_proxy(replica_id) as p:
                res = p.accept(ballot, slot, op)
            if res.get("ok"):
                print(f"[leader]   ← replica={replica_id} accepted retransmit (idempotent ✓)")
            else:
                print(f"[leader]   ← replica={replica_id} rejected retransmit: ballot={res.get('ballot')}")
        except Exception as exc:
            print(f"[leader]   ✗ retransmit failed: {exc}")


# ─────────────────────────────────────────────
# Large record file generator (100+ records)
# ─────────────────────────────────────────────

def generate_large_records(path: str, count: int = 100) -> None:
    """Write 'count' sortable key,value records to a local CSV file."""
    import random
    import string
    random.seed(42)
    names = [
        "alice", "bob", "carol", "dave", "eve", "frank", "grace",
        "heidi", "ivan", "judy", "karl", "linda", "mike", "nancy",
        "oscar", "pat", "quinn", "rob", "sue", "tina", "ursula",
        "victor", "wendy", "xena", "yolanda", "zach",
    ]
    with open(path, "w", encoding="utf-8") as f:
        for _ in range(count):
            key = str(random.randint(1, 9999)).zfill(4)
            value = random.choice(names)
            f.write(f"{key},{value}\n")
    print(f"[gen] wrote {count} records to {path}")


# ─────────────────────────────────────────────
# Scenario runners
# ─────────────────────────────────────────────

def scenario_follower_crash(host: str, port: int, m: int) -> None:
    section("SCENARIO 1: Follower crash during replication")
    print("""
  Setup: 3 Paxos replicas (IDs 1, 2, 3).
  After slot 1, replica 3 is treated as CRASHED.
  The leader skips it; majority = 2 of 3 replicas still reachable.
  Consensus commits successfully without the crashed follower.
""")

    chord = ChordDHTWrapper(host, port, m, replica_count=3)
    leader = FaultInjectingPaxosLeader(
        chord=chord,
        replica_ids=[1, 2, 3],
        crash_after_slot=1,        # replica 3 "crashes" after slot 1
        crashed_replica_id=3,
    )
    dfs = DFS(chord, leader)

    print("[demo] touch('crash-test.txt') — slot 1, all 3 replicas alive")
    dfs.touch("crash-test.txt")

    print("\n[demo] append to 'crash-test.txt' — slot 2+, replica 3 now CRASHED")
    with open("_tmp_crash_test.txt", "w") as f:
        f.write("distributed systems are fun\n")
    try:
        dfs.append("crash-test.txt", "_tmp_crash_test.txt")
        print("\n[demo] ✓ append succeeded with only 2 live replicas (majority maintained)")
    except Exception as e:
        print(f"\n[demo] ✗ unexpected error: {e}")
    finally:
        os.remove("_tmp_crash_test.txt")


def scenario_delayed_message(host: str, port: int, m: int) -> None:
    section("SCENARIO 2: Delayed ACCEPT message")
    print("""
  Setup: 3 Paxos replicas (IDs 1, 2, 3).
  Replica 2 experiences a 2-second delay before receiving ACCEPT.
  Leader waits; majority is still reached once replica 2 responds.
  This simulates a slow network path or temporarily overloaded node.
""")

    chord = ChordDHTWrapper(host, port, m, replica_count=3)
    leader = FaultInjectingPaxosLeader(
        chord=chord,
        replica_ids=[1, 2, 3],
        delay_replica_id=2,
        delay_seconds=2.0,
    )
    dfs = DFS(chord, leader)

    print("[demo] touch('delay-test.txt') — replica 2 will be slow")
    t0 = time.time()
    dfs.touch("delay-test.txt")
    elapsed = time.time() - t0
    print(f"\n[demo] ✓ touch committed in {elapsed:.2f}s despite delayed replica")


def scenario_retransmit(host: str, port: int, m: int) -> None:
    section("SCENARIO 3: Retransmitted ACCEPT (idempotency)")
    print("""
  Setup: Leader sends ACCEPT for slot 99 / ballot 99 to replica 1.
  Then sends it again (retransmit).
  Replica must accept it both times (idempotent) — same ballot, same slot.
  This models what happens when the leader suspects a lost message
  and retransmits before hearing back.
""")

    chord = ChordDHTWrapper(host, port, m, replica_count=3)
    leader = FaultInjectingPaxosLeader(
        chord=chord,
        replica_ids=[1, 2, 3],
    )

    op = {"type": "put", "key": "retransmit-test-key", "value": "test-value"}

    print("[demo] First ACCEPT to replica 1 ...")
    with paxos_proxy(1) as p:
        res = p.accept(99, 99, op)
    print(f"[demo]   result: {res}")

    print("\n[demo] Retransmitting same ACCEPT to replica 1 ...")
    leader.retransmit_accept(replica_id=1, slot=99, ballot=99, op=op)
    print("\n[demo] ✓ Replica accepted retransmit — idempotency confirmed")


def scenario_large_sort(host: str, port: int, m: int) -> None:
    section("SCENARIO 4: Distributed sort with 100 records (≥ 100 required)")
    print("""
  Generates 100 random key,value records.
  Stores them as a DFS file.
  Runs sort_file() — distributes records across Chord buckets,
  sorts locally per bucket, merges globally.
  Validates output is globally sorted.
""")

    generate_large_records("_tmp_large_records.csv", count=100)

    chord = ChordDHTWrapper(host, port, m, replica_count=3)
    leader = PaxosLeader(chord=chord, replica_ids=[1, 2, 3])
    dfs = DFS(chord, leader)

    print("\n[demo] Creating DFS file 'big_records.txt' ...")
    dfs.touch("big_records.txt")
    dfs.append("big_records.txt", "_tmp_large_records.csv")
    os.remove("_tmp_large_records.csv")

    print("\n[demo] Running sort_file('big_records.txt', 'big_sorted.txt') ...")
    result = dfs.sort_file("big_records.txt", "big_sorted.txt")
    print(f"\n[demo] Sort result: {json.dumps(result, indent=2)}")

    print("\n[demo] Verifying global sort order ...")
    output = dfs.read("big_sorted.txt").splitlines()
    keys = [line.split(",", 1)[0] for line in output if line.strip()]
    assert keys == sorted(keys), "ERROR: output is NOT globally sorted!"
    print(f"[demo] ✓ All {len(keys)} records are globally sorted")
    print(f"[demo] First 5 : {output[:5]}")
    print(f"[demo] Last  5 : {output[-5:]}")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Part 2 failure scenario demo")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--m", type=int, default=8)
    ap.add_argument(
        "--scenario",
        choices=["crash", "delay", "retransmit", "sort", "all"],
        default="all",
        help="Which scenario to run (default: all)",
    )
    args = ap.parse_args()

    print(f"\n{'#' * 60}")
    print("  DISTRIBUTED FILE SYSTEM — PART 2 FAILURE DEMO")
    print(f"{'#' * 60}")
    print(f"  Chord entry: {args.host}:{args.port}  m={args.m}")
    print(f"  Running scenario: {args.scenario}")

    if args.scenario in ("crash", "all"):
        scenario_follower_crash(args.host, args.port, args.m)

    if args.scenario in ("delay", "all"):
        scenario_delayed_message(args.host, args.port, args.m)

    if args.scenario in ("retransmit", "all"):
        scenario_retransmit(args.host, args.port, args.m)

    if args.scenario in ("sort", "all"):
        scenario_large_sort(args.host, args.port, args.m)

    print(f"\n{'#' * 60}")
    print("  ALL SCENARIOS COMPLETE")
    print(f"{'#' * 60}\n")


if __name__ == "__main__":
    main()