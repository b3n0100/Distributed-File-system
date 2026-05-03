# Distributed File System over Chord — Design Report
### CECS 327 — Parts 1 & 2

---

## 1. Chord Integration

Chord is the backbone of this project. Every file page, metadata object, and sorted bucket gets placed on the ring by hashing its key and routing to the responsible successor node. No central directory is needed — the ring handles placement automatically.

Each node gets a numeric ID from consistent hashing of its host and port:

```
node_id = SHA1("host:port") % 2^m
```

File pages and sort buckets get deterministic keys the same way:

```
metadata:hello.txt
hello.txt:0
sorted-bucket:big_sorted.txt:122
```

Each key is hashed and routed to the correct successor. From our terminal output, node 122 joins through node 165 and learns its successor immediately:

```
[node 122] registered chord.node.122 -> PYRO:obj_89ce4d92...@127.0.0.1:9001
[node 122] joined via 165; successor=165
[node 122] running; ctrl-c to stop
```

This is standard Chord behavior — the joining node contacts a known peer, finds its successor, and begins stabilization. Finger tables are fixed in the background so lookups stay O(log n).

The key advantage here is that the DFS never needs to know which physical node stores a given page. It just hashes the key and lets Chord figure out the rest.

---

## 2. Metadata and Page Design

Each distributed file is represented as two separate layers: a metadata object and one or more page objects. Keeping these separate was one of the most important design decisions we made.

**Metadata** is stored under a deterministic key:

```
metadata:<filename>
```

It tracks everything about the file's structure — filename, size, page count, page GUIDs, replica locations, and a version number. From a live `stat` call on `hello.txt`:

```json
{
  "type": "metadata",
  "filename": "hello.txt",
  "size_bytes": 54,
  "num_pages": 1,
  "pages": [
    {
      "page_no": 0,
      "guid": "hello.txt:0",
      "size": 54,
      "replicas": [
        "hello.txt:0|replica|0",
        "hello.txt:0|replica|1",
        "hello.txt:0|replica|2"
      ]
    }
  ],
  "version": 2
}
```

**Pages** are stored separately under keys like `hello.txt:0`. The actual file bytes live here, base64-encoded so they survive JSON serialization cleanly.

When you append to a file, the system writes the new page first, then updates the metadata with the new page descriptor and increments the version. You can see this ordering in the Paxos commit log:

```
[leader] COMMIT slot=3 ballot=3 op=put key=hello.txt:0
[leader] COMMIT slot=4 ballot=4 op=put key=metadata:hello.txt
```

The page goes in first, then the metadata update that points to it. This matters because if the system crashes between the two, the worst case is an unreferenced page — the metadata never pointed to it, so the file stays consistent from the client's perspective.

Separating metadata from content also makes replication much simpler. Metadata objects are small JSON blobs that are cheap to replicate and version. Pages can be large, but they are write-once — once a page is appended it never changes, which makes consistency much easier to reason about.

---

## 3. Distributed Sorting Strategy

The sort operation uses a bucket-based approach where records are partitioned across Chord nodes by key, sorted locally per bucket, then merged into a globally sorted output file.

**Step 1 — Read all pages of the input file**

The client reads every page of the input DFS file and parses each `key,value` record.

**Step 2 — Partition records into buckets**

Each record key is hashed to find the responsible Chord successor:

```python
owner = chord._find_responsible("sort-key:" + key)
```

Records with the same owner get grouped into the same bucket. From our 100-record demo:

```json
{
  "buckets": {
    "225": 30,
    "243": 8,
    "122": 63,
    "165": 15,
    "169": 4
  },
  "records": 100,
  "output": "big_sorted.txt"
}
```

100 records were distributed across all 5 Chord nodes, with each node responsible for the records whose keys hash into its range.

**Step 3 — Sort locally per bucket**

Each bucket's records are sorted by key independently. The sorted bucket is stored as a distributed object:

```
[leader] COMMIT slot=5 ballot=5 op=put key=sorted-bucket:big_sorted.txt:122
[leader] COMMIT slot=6 ballot=6 op=put key=sorted-bucket:big_sorted.txt:165
[leader] COMMIT slot=7 ballot=7 op=put key=sorted-bucket:big_sorted.txt:169
[leader] COMMIT slot=8 ballot=8 op=put key=sorted-bucket:big_sorted.txt:225
[leader] COMMIT slot=9 ballot=9 op=put key=sorted-bucket:big_sorted.txt:243
```

**Step 4 — Merge into globally sorted output**

The client merges all bucket results into one globally sorted list and writes it as a new DFS file. The output is validated before the operation returns:

```
[demo] ✓ All 100 records are globally sorted
[demo] First 5 : ['0054,xena', '0107,yolanda', '0189,victor', '0521,alice', '0526,zach']
[demo] Last  5 : ['9639,rob', '9655,ivan', '9675,nancy', '9814,karl', '9864,alice']
```

The sort manifest is stored in the output file's metadata for debugging and auditing purposes.

---

## 4. Replication Strategy

Every DFS object — metadata and pages — is replicated three times. The replication factor R=3 was chosen because it is the minimum needed to tolerate one node failure while still maintaining a majority for Paxos.

Replica keys are derived deterministically from the logical key:

```
<logical_key>|replica|0
<logical_key>|replica|1
<logical_key>|replica|2
```

Each replica key is routed through Chord independently, so with enough peers in the ring the three copies end up on different physical nodes. This is visible in the stat output for every file:

```json
"replicas": [
  "hello.txt:0|replica|0",
  "hello.txt:0|replica|1",
  "hello.txt:0|replica|2"
]
```

On write, all three replicas are written before the operation is considered complete. On read, the system tries each replica key in order and returns the first successful result. This means reads survive a single node failure without any extra coordination.

We chose page-level replication rather than full-file replication because it fits naturally into the existing page descriptor model. The metadata already tracks per-page GUIDs, so adding replica keys per page was straightforward. It also means that for large files, individual pages can be recovered independently rather than needing to re-replicate the entire file.

The one limitation of this approach is that replica repair is not automatic. If a node goes down and comes back, the system does not detect that its replicas are stale and rebuild them. This would require a background repair process that we did not implement.

---

## 5. Paxos Message Flow

Paxos is used to serialize all DFS metadata updates across the three replicas. Every call to `touch`, `append`, `delete_file`, and `sort_file` goes through the Paxos leader before anything is written to Chord.

The protocol follows a simplified two-phase commit:

**Phase 1 — ACCEPT**

The leader assigns a slot and ballot number to the operation and sends ACCEPT to all replicas:

```
[leader]   → ACCEPT(ballot=1, slot=1) to replica=1
[leader]   ← ACK from replica=1
[leader]   → ACCEPT(ballot=1, slot=1) to replica=2
[leader]   ← ACK from replica=2
[leader]   → ACCEPT(ballot=1, slot=1) to replica=3
[leader]   ← ACK from replica=3
[leader]   accepts=3 needed=2
```

A replica accepts if the incoming ballot is greater than or equal to any ballot it has already seen for that slot. This ensures that a higher-ballot leader can always take over.

**Phase 2 — LEARN**

Once a majority of replicas have accepted, the leader sends LEARN to those replicas:

```
[leader]   → LEARN(ballot=1, slot=1) to replica=1
[leader]   ← LEARNED by replica=1
[leader]   → LEARN(ballot=1, slot=1) to replica=2
[leader]   ← LEARNED by replica=2
[leader] ✓ COMMIT slot=1 ballot=1 op=put key=metadata:hello.txt
```

Once a majority have learned, the operation is committed and applied to Chord. All replicas that learned the operation have it in their committed log, so even if the leader crashes at this point, the operation is durable.

From the replica's perspective, replica 2 shows both phases for every slot:

```
[replica 2] ACCEPT slot=1 ballot=1 op=put key=metadata:hello.txt
[replica 2] LEARN  slot=1 ballot=1 op=put key=metadata:hello.txt
```

Operations are ordered by slot number, which means every replica applies them in the same sequence regardless of timing. This is the core guarantee Paxos provides — not just that all replicas eventually agree, but that they agree on the same order.

---

## 6. Failure Scenario — Follower Crash During Replication

For Part 2, we demonstrated a follower crash mid-session using `demo_failure.py`. The scenario works as follows: replica 3 is treated as crashed after slot 1, and the leader must continue committing using only replicas 1 and 2.

**Slot 1 — all three replicas alive:**

```
[leader] → PROPOSE slot=1 ballot=1 op=put key=metadata:crash-test.txt
[leader]   → ACCEPT(ballot=1, slot=1) to replica=1
[leader]   ← ACK from replica=1
[leader]   → ACCEPT(ballot=1, slot=1) to replica=2
[leader]   ← ACK from replica=2
[leader]   → ACCEPT(ballot=1, slot=1) to replica=3
[leader]   ← ACK from replica=3
[leader]   accepts=3 needed=2
[leader] ✓ COMMIT slot=1 ballot=1 op=put key=metadata:crash-test.txt
```

**Slot 2+ — replica 3 crashed, leader skips it:**

```
[leader] → PROPOSE slot=2 ballot=2 op=put key=dfs:index
[leader]   → ACCEPT(ballot=2, slot=2) to replica=1
[leader]   ← ACK from replica=1
[leader]   → ACCEPT(ballot=2, slot=2) to replica=2
[leader]   ← ACK from replica=2
[leader]   ✗ replica=3 is CRASHED — skipping
[leader]   accepts=2 needed=2
[leader] ✓ COMMIT slot=2 ballot=2 op=put key=dfs:index
```

The system successfully committed every subsequent operation with only 2 of 3 replicas. This is exactly the fault tolerance guarantee Paxos provides — as long as a majority (2 of 3) remain available, the system makes progress.

The final confirmation from the demo:

```
[demo] ✓ append succeeded with only 2 live replicas (majority maintained)
```

This scenario directly maps to the course material on crash failures, majority-based commitment, and why three replicas are the minimum useful replication factor.

---

## 7. Failure Assumptions

We designed around crash-stop failures only. Nodes stop and stay stopped — they do not send corrupted messages or behave maliciously. This is a reasonable assumption for a local network environment and keeps the protocol analysis tractable.

Specific assumptions:

- A majority of Paxos replicas (at least 2 of 3) remain alive at all times
- Messages may be delayed or reordered, but are eventually delivered
- Chord nodes may leave the ring; stabilization will eventually repair successor pointers
- No Byzantine behavior — a node either responds correctly or not at all

The system is fault tolerant up to one replica failure. If two of three Paxos replicas crash simultaneously, consensus stops and the system becomes unavailable. If all replicas of a given page are lost, that data is unrecoverable. These are known limitations of the design.

---

## 8. Limitations and Future Improvements

**Current limitations:**

Our Paxos implementation is a simplified single-leader protocol. There is no leader election — if the leader process crashes, the system stops. A full Multi-Paxos implementation would handle leader failure by having replicas detect a timeout and elect a new leader deterministically.

Chord stabilization works correctly but is not aggressive about rebalancing after node departures. In a production system, data would need to migrate to new successors when nodes leave.

Replica repair is not implemented. The system tracks replica locations in metadata, but does not detect or rebuild stale or missing replicas after a node recovers. This would require a background process comparing replica states.

There is no persistent disk storage. All state lives in memory, so a full system restart loses all data. Adding disk-backed storage to each Chord node would make the system durable across restarts.

**Future improvements:**

- Full Multi-Paxos with timeout-based leader election and deterministic takeover
- Background replica healing that detects missing replicas and rebuilds them
- Persistent storage on each Chord node using a simple key-value store like SQLite
- Automatic data migration when nodes join or leave the ring
- Stronger concurrency control for simultaneous client writes to the same file

Despite these limitations, the system correctly demonstrates the core ideas: Chord-based distributed storage, page-level replication, Paxos-ordered metadata updates, distributed sorting across ring nodes, and continued availability under single-node failure.

---

## 9. Test Evidence

### Part 1 — DFS operations

```
=== touch ===
[leader] COMMIT slot=1 ballot=1 op=put key=metadata:hello.txt
[leader] COMMIT slot=2 ballot=2 op=put key=dfs:index
True

=== append ===
[leader] COMMIT slot=3 ballot=3 op=put key=hello.txt:0
[leader] COMMIT slot=4 ballot=4 op=put key=metadata:hello.txt

=== ls ===
['hello.txt']

=== stat ===
{
  "type": "metadata",
  "filename": "hello.txt",
  "size_bytes": 54,
  "num_pages": 1,
  "pages": [
    {
      "page_no": 0,
      "guid": "hello.txt:0",
      "size": 54,
      "replicas": [
        "hello.txt:0|replica|0",
        "hello.txt:0|replica|1",
        "hello.txt:0|replica|2"
      ]
    }
  ],
  "version": 2
}

=== read ===
line one
line two
line three
line four
line five

=== head 3 ===
line one
line two
line three

=== tail 2 ===
line four
line five

=== sort (5 records) ===
{
  "buckets": { "225": 3, "122": 2 },
  "records": 5,
  "output": "records.sorted.txt"
}
0005,zoe
0012,alice
0042,bob
0042,bobby
0190,carol
```

### Part 2 — 100-record distributed sort

```
[demo] ✓ All 100 records are globally sorted
[demo] First 5 : ['0054,xena', '0107,yolanda', '0189,victor', '0521,alice', '0526,zach']
[demo] Last  5 : ['9639,rob', '9655,ivan', '9675,nancy', '9814,karl', '9864,alice']

Buckets:
  node 122 : 63 records
  node 165 : 15 records
  node 169 :  4 records
  node 225 : 30 records
  node 243 :  8 records
```

### Part 2 — Follower crash scenario

```
[leader] ✓ COMMIT slot=1 ballot=1 op=put key=metadata:crash-test.txt  (all 3 alive)
[leader]   ✗ replica=3 is CRASHED — skipping
[leader]   accepts=2 needed=2
[leader] ✓ COMMIT slot=2 ballot=2 op=put key=dfs:index  (2 of 3 alive)
[leader] ✓ COMMIT slot=3 ballot=3 op=put key=crash-test.txt:0
[leader] ✓ COMMIT slot=4 ballot=4 op=put key=metadata:crash-test.txt
[demo] ✓ append succeeded with only 2 live replicas (majority maintained)
```

### Paxos replica log (replica 2)

```
[replica 2] ACCEPT slot=1 ballot=1 op=put key=metadata:hello.txt
[replica 2] LEARN  slot=1 ballot=1 op=put key=metadata:hello.txt
[replica 2] ACCEPT slot=2 ballot=2 op=put key=dfs:index
[replica 2] LEARN  slot=2 ballot=2 op=put key=dfs:index
[replica 2] ACCEPT slot=3 ballot=3 op=put key=hello.txt:0
[replica 2] LEARN  slot=3 ballot=3 op=put key=hello.txt:0
[replica 2] ACCEPT slot=4 ballot=4 op=put key=metadata:hello.txt
[replica 2] LEARN  slot=4 ballot=4 op=put key=metadata:hello.txt
```