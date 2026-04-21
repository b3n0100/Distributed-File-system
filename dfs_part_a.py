from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import Pyro5.api as pyro


# ----------------------------
# Shared helpers
# ----------------------------

def sha1_int(s: str) -> int:
    return int(hashlib.sha1(s.encode("utf-8")).hexdigest(), 16)


def node_id_for(host: str, port: int, m: int) -> int:
    return sha1_int(f"{host}:{port}") % (2 ** m)


def proxy_for(node_id: int):
    return pyro.Proxy(f"PYRONAME:chord.node.{node_id}")


def paxos_proxy(replica_id: int):
    return pyro.Proxy(f"PYRONAME:paxos.replica.{replica_id}")


# ----------------------------
# Chord wrapper + replication
# ----------------------------

class ChordDHTWrapper:
    def __init__(self, entry_host: str, entry_port: int, m: int = 8, replica_count: int = 3):
        self.m = m
        self.ring_size = 2 ** m
        self.entry_id = node_id_for(entry_host, entry_port, m)
        self.replica_count = replica_count

    def _find_responsible(self, key: str) -> int:
        key_id = sha1_int(key) % self.ring_size
        with proxy_for(self.entry_id) as p:
            result = p.find_successor(key_id)
        return result["node_id"]

    def replica_keys(self, logical_key: str) -> List[str]:
        return [f"{logical_key}|replica|{i}" for i in range(self.replica_count)]

    def put_physical(self, key: str, value: str) -> None:
        node_id = self._find_responsible(key)
        with proxy_for(node_id) as p:
            p.store_put(key, value)

    def get_physical(self, key: str) -> Optional[str]:
        node_id = self._find_responsible(key)
        with proxy_for(node_id) as p:
            return p.store_get(key)

    def delete_physical(self, key: str) -> None:
        node_id = self._find_responsible(key)
        with proxy_for(node_id) as p:
            p.store_delete(key)

    def put_replicated(self, logical_key: str, value: str) -> List[str]:
        keys = self.replica_keys(logical_key)
        for key in keys:
            self.put_physical(key, value)
        return keys

    def get_replicated(self, logical_key: str) -> Optional[str]:
        for key in self.replica_keys(logical_key):
            value = self.get_physical(key)
            if value is not None:
                return value
        return None

    def delete_replicated(self, logical_key: str) -> List[str]:
        keys = self.replica_keys(logical_key)
        for key in keys:
            self.delete_physical(key)
        return keys


# ----------------------------
# Simplified Paxos leader
# ----------------------------

@dataclass
class PaxosLeader:
    chord: ChordDHTWrapper
    replica_ids: List[int]
    ballot: int = 1
    next_slot: int = 1

    def _majority(self) -> int:
        return len(self.replica_ids) // 2 + 1

    def _apply(self, op: Dict[str, Any]) -> None:
        if op["type"] == "put":
            self.chord.put_replicated(op["key"], op["value"])
        elif op["type"] == "delete":
            self.chord.delete_replicated(op["key"])
        else:
            raise ValueError(f"unknown operation type: {op['type']}")

    def _commit_operation(self, op: Dict[str, Any]) -> bool:
        slot = self.next_slot
        ballot = self.ballot
        accepts = 0
        acceptors: List[int] = []
        for rid in self.replica_ids:
            try:
                with paxos_proxy(rid) as p:
                    res = p.accept(ballot, slot, op)
                if res.get("ok"):
                    accepts += 1
                    acceptors.append(rid)
            except Exception as exc:
                print(f"[leader] ACCEPT failed replica={rid}: {exc}")
        if accepts < self._majority():
            print(f"[leader] failed to reach ACCEPT majority for slot={slot}")
            self.ballot += 1
            return False

        learns = 0
        for rid in acceptors:
            try:
                with paxos_proxy(rid) as p:
                    res = p.learn(ballot, slot, op)
                if res.get("ok"):
                    learns += 1
            except Exception as exc:
                print(f"[leader] LEARN failed replica={rid}: {exc}")
        if learns < self._majority():
            print(f"[leader] failed to reach LEARN majority for slot={slot}")
            self.ballot += 1
            return False

        print(f"[leader] COMMIT slot={slot} ballot={ballot} op={op['type']} key={op['key']}")
        self._apply(op)
        self.next_slot += 1
        self.ballot += 1
        return True

    def propose(self, key: str, value: str) -> bool:
        return self._commit_operation({"type": "put", "key": key, "value": value})

    def propose_delete(self, key: str) -> bool:
        return self._commit_operation({"type": "delete", "key": key})


# ----------------------------
# DFS
# ----------------------------

class DFS:
    PAGE_SIZE = 4096

    def __init__(self, chord: ChordDHTWrapper, paxos_node: Optional[PaxosLeader] = None):
        self.chord = chord
        self.paxos = paxos_node

    def _metadata_key(self, filename: str) -> str:
        return "metadata:" + filename

    def _page_key(self, filename: str, page_no: int) -> str:
        return f"{filename}:{page_no}"

    def _index_key(self) -> str:
        return "dfs:index"

    def _sorted_bucket_key(self, output_filename: str, bucket_owner: int) -> str:
        return f"sorted-bucket:{output_filename}:{bucket_owner}"

    def _get_object(self, logical_key: str) -> Optional[Dict[str, Any]]:
        value = self.chord.get_replicated(logical_key)
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return json.loads(value)

    def _replicated_put(self, logical_key: str, value_obj: Dict[str, Any]) -> List[str]:
        serialized = json.dumps(value_obj)
        if self.paxos:
            success = self.paxos.propose(logical_key, serialized)
            if not success:
                raise RuntimeError("Paxos consensus failed to commit update.")
            return self.chord.replica_keys(logical_key)
        return self.chord.put_replicated(logical_key, serialized)

    def _replicated_delete(self, logical_key: str) -> List[str]:
        if self.paxos:
            success = self.paxos.propose_delete(logical_key)
            if not success:
                raise RuntimeError("Paxos consensus failed to commit delete.")
            return self.chord.replica_keys(logical_key)
        return self.chord.delete_replicated(logical_key)

    def _get_index(self) -> Dict[str, List[str]]:
        index = self._get_object(self._index_key())
        if index is None:
            return {"files": []}
        return index

    def _add_to_index(self, filename: str) -> None:
        index = self._get_index()
        if filename not in index["files"]:
            index["files"].append(filename)
            index["files"].sort()
            self._replicated_put(self._index_key(), index)

    def _remove_from_index(self, filename: str) -> None:
        index = self._get_index()
        if filename in index["files"]:
            index["files"].remove(filename)
            self._replicated_put(self._index_key(), index)

    def touch(self, filename: str) -> bool:
        if self._get_object(self._metadata_key(filename)) is not None:
            return False
        metadata = {
            "type": "metadata",
            "filename": filename,
            "size_bytes": 0,
            "num_pages": 0,
            "pages": [],
            "version": 1,
        }
        self._replicated_put(self._metadata_key(filename), metadata)
        self._add_to_index(filename)
        return True

    def append(self, filename: str, local_path: str) -> bool:
        metadata = self._get_object(self._metadata_key(filename))
        if metadata is None:
            raise FileNotFoundError(f"File '{filename}' not found.")
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file '{local_path}' does not exist.")

        with open(local_path, "rb") as infile:
            while True:
                chunk = infile.read(self.PAGE_SIZE)
                if not chunk:
                    break
                page_no = metadata["num_pages"]
                page_key = self._page_key(filename, page_no)
                page_obj = {
                    "type": "page",
                    "filename": filename,
                    "page_no": page_no,
                    "data": base64.b64encode(chunk).decode("utf-8"),
                }
                replica_keys = self._replicated_put(page_key, page_obj)
                metadata["pages"].append({
                    "page_no": page_no,
                    "guid": page_key,
                    "size": len(chunk),
                    "replicas": replica_keys,
                })
                metadata["num_pages"] += 1
                metadata["size_bytes"] += len(chunk)

        metadata["version"] += 1
        self._replicated_put(self._metadata_key(filename), metadata)
        return True

    def _read_file_bytes(self, filename: str) -> bytes:
        metadata = self._get_object(self._metadata_key(filename))
        if metadata is None:
            raise FileNotFoundError(filename)
        content = bytearray()
        for p_desc in sorted(metadata["pages"], key=lambda x: x["page_no"]):
            page = self._get_object(p_desc["guid"])
            if page is None:
                raise RuntimeError(f"Missing page {p_desc['page_no']} for '{filename}'.")
            content.extend(base64.b64decode(page["data"]))
        return bytes(content)

    def read(self, filename: str) -> str:
        return self._read_file_bytes(filename).decode("utf-8", errors="ignore")

    def head(self, filename: str, n: int) -> str:
        if n < 0:
            raise ValueError("n must be nonnegative")
        if n == 0:
            return ""
        return "\n".join(self.read(filename).splitlines()[:n])

    def tail(self, filename: str, n: int) -> str:
        if n < 0:
            raise ValueError("n must be nonnegative")
        if n == 0:
            return ""
        return "\n".join(self.read(filename).splitlines()[-n:])

    def delete_file(self, filename: str) -> bool:
        metadata = self._get_object(self._metadata_key(filename))
        if metadata is None:
            return False
        for p_desc in metadata["pages"]:
            self._replicated_delete(p_desc["guid"])
        self._replicated_delete(self._metadata_key(filename))
        self._remove_from_index(filename)
        return True

    def stat(self, filename: str) -> Dict[str, Any]:
        metadata = self._get_object(self._metadata_key(filename))
        if metadata is None:
            raise FileNotFoundError(filename)
        return metadata

    def ls(self) -> List[str]:
        return self._get_index()["files"]

    def _bucket_owner(self, record_key: str) -> int:
        logical = f"sort-key:{record_key}"
        return self.chord._find_responsible(logical)

    def sort_file(self, filename: str, output_filename: str) -> Dict[str, Any]:
        raw_text = self.read(filename)
        if not raw_text.strip():
            self.touch(output_filename)
            return {"buckets": {}, "records": 0, "output": output_filename}

        buckets: Dict[int, List[Tuple[str, str]]] = {}
        record_count = 0
        for line in raw_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "," not in line:
                raise ValueError(f"Invalid record without comma: {line!r}")
            key, value = line.split(",", 1)
            owner = self._bucket_owner(key)
            buckets.setdefault(owner, []).append((key, value))
            record_count += 1

        bucket_manifest = []
        globally_sorted: List[Tuple[str, str]] = []
        for owner in sorted(buckets.keys()):
            local_records = sorted(buckets[owner], key=lambda kv: kv[0])
            bucket_key = self._sorted_bucket_key(output_filename, owner)
            bucket_obj = {
                "type": "sorted_bucket",
                "output_filename": output_filename,
                "owner": owner,
                "records": [f"{k},{v}" for k, v in local_records],
            }
            replica_keys = self._replicated_put(bucket_key, bucket_obj)
            bucket_manifest.append({
                "owner": owner,
                "bucket_key": bucket_key,
                "replicas": replica_keys,
                "record_count": len(local_records),
            })
            globally_sorted.extend(local_records)

        globally_sorted = sorted(globally_sorted, key=lambda kv: kv[0])
        output_temp = f"{output_filename}.tmp.sorted"
        with open(output_temp, "w", encoding="utf-8") as out:
            for key, value in globally_sorted:
                out.write(f"{key},{value}\n")

        if self._get_object(self._metadata_key(output_filename)) is None:
            self.touch(output_filename)
        else:
            self.delete_file(output_filename)
            self.touch(output_filename)

        self.append(output_filename, output_temp)
        os.remove(output_temp)

        out_meta = self._get_object(self._metadata_key(output_filename))
        out_meta["sorted_from"] = filename
        out_meta["sort_manifest"] = bucket_manifest
        out_meta["version"] += 1
        self._replicated_put(self._metadata_key(output_filename), out_meta)

        check = self.read(output_filename).splitlines()
        keys = [line.split(",", 1)[0] for line in check if line]
        assert keys == sorted(keys), "sorted output is not globally ordered"

        return {
            "buckets": {owner: len(rows) for owner, rows in buckets.items()},
            "records": record_count,
            "output": output_filename,
        }


# ----------------------------
# Demo / CLI
# ----------------------------


def make_client(host: str, port: int, m: int, use_paxos: bool, replica_ids: List[int]) -> DFS:
    chord = ChordDHTWrapper(entry_host=host, entry_port=port, m=m, replica_count=3)
    paxos = PaxosLeader(chord=chord, replica_ids=replica_ids) if use_paxos else None
    return DFS(chord, paxos)


def run_demo(host: str, port: int, m: int, use_paxos: bool, replica_ids: List[int]) -> None:
    dfs = make_client(host, port, m, use_paxos, replica_ids)

    with open("sample.txt", "w", encoding="utf-8") as f:
        f.write("line one\nline two\nline three\nline four\nline five\n")

    with open("records.csv", "w", encoding="utf-8") as f:
        f.write("0042,bob\n0012,alice\n0190,carol\n0005,zoe\n0042,bobby\n")

    print("\n=== touch ===")
    print(dfs.touch("hello.txt"))

    print("\n=== append ===")
    dfs.append("hello.txt", "sample.txt")

    print("\n=== ls ===")
    print(dfs.ls())

    print("\n=== stat ===")
    print(json.dumps(dfs.stat("hello.txt"), indent=2))

    print("\n=== read ===")
    print(dfs.read("hello.txt"))

    print("\n=== head 3 ===")
    print(dfs.head("hello.txt", 3))

    print("\n=== tail 2 ===")
    print(dfs.tail("hello.txt", 2))

    print("\n=== sort ===")
    dfs.touch("records.txt")
    dfs.append("records.txt", "records.csv")
    result = dfs.sort_file("records.txt", "records.sorted.txt")
    print(json.dumps(result, indent=2))
    print(dfs.read("records.sorted.txt"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--m", type=int, default=8)
    ap.add_argument("--no-paxos", action="store_true")
    ap.add_argument("--replica-ids", default="1,2,3")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--touch")
    ap.add_argument("--append", nargs=2, metavar=("FILENAME", "LOCAL_PATH"))
    ap.add_argument("--read")
    ap.add_argument("--head", nargs=2, metavar=("FILENAME", "N"))
    ap.add_argument("--tail", nargs=2, metavar=("FILENAME", "N"))
    ap.add_argument("--delete-file")
    ap.add_argument("--stat")
    ap.add_argument("--ls", action="store_true")
    ap.add_argument("--sort-file", nargs=2, metavar=("INPUT", "OUTPUT"))
    args = ap.parse_args()

    replica_ids = [int(x) for x in args.replica_ids.split(",") if x.strip()]
    dfs = make_client(args.host, args.port, args.m, not args.no_paxos, replica_ids)

    if args.demo:
        run_demo(args.host, args.port, args.m, not args.no_paxos, replica_ids)
        return
    if args.touch:
        print(dfs.touch(args.touch))
        return
    if args.append:
        print(dfs.append(args.append[0], args.append[1]))
        return
    if args.read:
        print(dfs.read(args.read))
        return
    if args.head:
        print(dfs.head(args.head[0], int(args.head[1])))
        return
    if args.tail:
        print(dfs.tail(args.tail[0], int(args.tail[1])))
        return
    if args.delete_file:
        print(dfs.delete_file(args.delete_file))
        return
    if args.stat:
        print(json.dumps(dfs.stat(args.stat), indent=2))
        return
    if args.ls:
        print(json.dumps(dfs.ls(), indent=2))
        return
    if args.sort_file:
        print(json.dumps(dfs.sort_file(args.sort_file[0], args.sort_file[1]), indent=2))
        return

    run_demo(args.host, args.port, args.m, not args.no_paxos, replica_ids)


if __name__ == "__main__":
    main()
