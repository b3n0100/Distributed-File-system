import os
import json
import hashlib
import base64
import Pyro5.api as pyro

#Chord

def sha1_int(s: str) -> int:
    return int(hashlib.sha1(s.encode("utf-8")).hexdigest(), 16)

def node_id_for(host: str, port: int, m: int) -> int:
    return sha1_int(f"{host}:{port}") % (2 ** m)

def proxy_for(node_id: int):
    return pyro.Proxy(f"PYRONAME:chord.node.{node_id}")


class ChordDHTWrapper:
    """
    Wraps the real Chord ring.
    find_successor routes us to the right node, then we store data there.
    Requires chord.py nodes to expose store_put / store_get / store_delete.
    """

    def __init__(self, entry_host: str, entry_port: int, m: int = 8):
        self.m = m
        self.ring_size = 2 ** m
        self.entry_id = node_id_for(entry_host, entry_port, m)

    def _find_responsible(self, key: str) -> int:
        key_id = sha1_int(key) % self.ring_size
        with proxy_for(self.entry_id) as p:
            result = p.find_successor(key_id)
        return result["node_id"]

    def put(self, key: str, value: str):
        node_id = self._find_responsible(key)
        with proxy_for(node_id) as p:
            p.store_put(key, value)

    def get(self, key: str):
        node_id = self._find_responsible(key)
        with proxy_for(node_id) as p:
            return p.store_get(key)

    def delete(self, key: str):
        node_id = self._find_responsible(key)
        with proxy_for(node_id) as p:
            p.store_delete(key)


#DFS

class DFS:
    PAGE_SIZE = 4096

    def __init__(self, chord, paxos_node=None):
        self.chord = chord
        self.paxos = paxos_node

    def _metadata_key(self, filename):
        return "metadata:" + filename

    def _page_key(self, filename, page_no):
        return f"{filename}:{page_no}"

    def _index_key(self):
        return "dfs:index"

    def _get_object(self, key):
        value = self.chord.get(key)
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return json.loads(value)

    def _replicated_put(self, key, value_obj):
        serialized = json.dumps(value_obj)
        if self.paxos:
            success = self.paxos.propose(key, serialized)
            if not success:
                raise RuntimeError("Paxos consensus failed to commit update.")
        else:
            self.chord.put(key, serialized)

    def _replicated_delete(self, key):
        if self.paxos:
            success = self.paxos.propose_delete(key)
            if not success:
                raise RuntimeError("Paxos consensus failed to commit delete.")
        else:
            self.chord.delete(key)

    def _get_index(self):
        index = self._get_object(self._index_key())
        if index is None:
            return {"files": []}
        return index

    def _add_to_index(self, filename):
        index = self._get_index()
        if filename not in index["files"]:
            index["files"].append(filename)
            index["files"].sort()
            self._replicated_put(self._index_key(), index)

    def _remove_from_index(self, filename):
        index = self._get_index()
        if filename in index["files"]:
            index["files"].remove(filename)
            self._replicated_put(self._index_key(), index)

    def touch(self, filename):
        if self._get_object(self._metadata_key(filename)) is not None:
            return False
        metadata = {
            "type": "metadata",
            "filename": filename,
            "size_bytes": 0,
            "num_pages": 0,
            "pages": [],
            "version": 1
        }
        self._replicated_put(self._metadata_key(filename), metadata)
        self._add_to_index(filename)
        return True

    def append(self, filename, local_path):
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
                    "data": base64.b64encode(chunk).decode("utf-8")
                }
                self._replicated_put(page_key, page_obj)
                metadata["pages"].append({
                    "page_no": page_no,
                    "guid": page_key,
                    "size": len(chunk)
                })
                metadata["num_pages"] += 1
                metadata["size_bytes"] += len(chunk)

        metadata["version"] += 1
        self._replicated_put(self._metadata_key(filename), metadata)
        return True

    def read(self, filename):
        metadata = self._get_object(self._metadata_key(filename))
        if metadata is None:
            raise FileNotFoundError(filename)
        full_content = b""
        for p_desc in sorted(metadata["pages"], key=lambda x: x["page_no"]):
            page = self._get_object(p_desc["guid"])
            if page is None:
                raise RuntimeError(f"Missing page {p_desc['page_no']} for '{filename}'.")
            full_content += base64.b64decode(page["data"])
        return full_content.decode("utf-8", errors="ignore")

    def head(self, filename, n):
        if n < 0:
            raise ValueError("n must be nonnegative")
        metadata = self._get_object(self._metadata_key(filename))
        if not metadata or not metadata["pages"]:
            return ""
        lines = []
        for p_desc in sorted(metadata["pages"], key=lambda x: x["page_no"]):
            page = self._get_object(p_desc["guid"])
            content = base64.b64decode(page["data"]).decode("utf-8", errors="ignore")
            lines.extend(content.splitlines())
            if len(lines) >= n:
                break
        return "\n".join(lines[:n])

    def tail(self, filename, n):
        if n < 0:
            raise ValueError("n must be nonnegative")
        metadata = self._get_object(self._metadata_key(filename))
        if not metadata or not metadata["pages"]:
            return ""
        lines = []
        for p_desc in sorted(metadata["pages"], key=lambda x: x["page_no"], reverse=True):
            page = self._get_object(p_desc["guid"])
            content = base64.b64decode(page["data"]).decode("utf-8", errors="ignore")
            lines = content.splitlines() + lines
            if len(lines) >= n:
                break
        return "\n".join(lines[-n:] if n > 0 else [])

    def delete_file(self, filename):
        metadata = self._get_object(self._metadata_key(filename))
        if metadata is None:
            return False
        for p_desc in metadata["pages"]:
            self._replicated_delete(p_desc["guid"])
        self._replicated_delete(self._metadata_key(filename))
        self._remove_from_index(filename)
        return True

    def stat(self, filename):
        metadata = self._get_object(self._metadata_key(filename))
        if metadata is None:
            raise FileNotFoundError(filename)
        return metadata

    def ls(self):
        return self._get_index()["files"]


#main

if __name__ == "__main__":
    import sys

    # python dfs_part_a.py [host] [port]
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9000

    print(f"Connecting to Chord ring via {host}:{port} ...")
    chord = ChordDHTWrapper(entry_host=host, entry_port=port, m=8)
    dfs = DFS(chord)

    with open("sample.txt", "w") as f:
        f.write("line one\nline two\nline three\nline four\nline five\n")

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

    print("\n=== delete ===")
    print(dfs.delete_file("hello.txt"))
    print(dfs.ls())