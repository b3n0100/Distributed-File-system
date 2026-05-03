# Distributed File System over Chord with Simplified Paxos

## Overview

This project extends a Chord ring into a small distributed file system. It supports:

- distributed file metadata and page storage through Chord routing
- deterministic metadata and page keys
- three-way replicated DFS objects
- a simplified Paxos-style commit path for DFS updates
- distributed sorting of `key,value` records into a new DFS file
- replication of metadata and pages with R=3
- Paxos-based agreement for all replicated DFS updates
- failure scenario demonstrations (follower crash, delayed message, retransmitted ACCEPT)

The implementation is intentionally small and demo-friendly. It is designed to satisfy the assignment structure rather than be production-grade.

## Files

- `Chord.py` - Chord DHT node implementation with local storage methods
- `dfs_part_a.py` - DFS client and DFS logic, including `sort_file`
- `paxos.py` - simplified Paxos replica service
- `demo_failure.py` - Part 2 failure scenario demo (follower crash, delay, retransmit, 100-record sort)
- `large_records.csv` -100-record input file for the distributed sort demo
- `README.md` - setup and usage

## Requirements

- Python 3.11+
- Pyro5

Install (Mac/Linux):

```bash
python -m venv .venv
source .venv/bin/activate
pip install Pyro5
```

Install (Windows):

```bash
python -m venv venv
venv\Scripts\Activate
pip install Pyro5
```

## Architecture

```text
DFS client
  -> simplified Paxos leader logic in dfs_part_a.py
  -> Chord routing layer
  -> local key-value storage on Chord peers
```

### Metadata and page model

Metadata key:

```text
metadata:<filename>
```

Page key:

```text
<filename>:<page_no>
```

Each logical DFS object is stored using three deterministic physical keys:

```text
<logical_key>|replica|0
<logical_key>|replica|1
<logical_key>|replica|2
```

This provides three replicated copies while still routing through Chord.

### Replication strategy (Part 2 — Part C)

Every DFS object (metadata and pages) is replicated with replication factor R=3.
Replica keys are derived deterministically from the logical key:

```text
<logical_key>|replica|0
<logical_key>|replica|1
<logical_key>|replica|2
```

Each replica key is routed independently through Chord, so replicas land on
different nodes when the ring has sufficient peers. On read, the system tries
each replica key in order and returns the first successful result, providing
fault-tolerant reads even when one replica node is unavailable.

### Simplified Paxos flow (Part 2 — Part D)

For every DFS update (touch, append, delete, sort):

1. leader chooses `(slot, ballot)`
2. leader sends `ACCEPT(ballot, slot, op)` to 3 Paxos replicas
3. replicas log the acceptance and respond with ACK
4. leader sends `LEARN(ballot, slot, op)` to replicas that accepted
5. once a majority has learned, the leader commits the DFS update to Chord

Paxos protects these operations:
- metadata update after `touch`
- metadata update after `append`
- metadata update after `delete_file`
- metadata update after `sort_file`
- all page writes

This mirrors the simplified assignment rule that operations are committed after
a majority learn them, and operations are ordered by slot number.

## How to run

Open 10 terminals total, or use a terminal multiplexer like tmux.
Activate the virtual environment in every terminal before running any command.

```bash
source .venv/bin/activate   # Mac/Linux
```

### Terminal 1 — Pyro name server

```bash
python -m Pyro5.nameserver
```

### Terminals 2 through 6 — Chord peers

```bash
python Chord.py node --port 9000 --bootstrap
python Chord.py node --port 9001 --join 127.0.0.1:9000
python Chord.py node --port 9002 --join 127.0.0.1:9000
python Chord.py node --port 9003 --join 127.0.0.1:9000
python Chord.py node --port 9004 --join 127.0.0.1:9000
```

Wait 1-2 seconds between each node so stabilization can run.

### Terminals 7 through 9 — Paxos replicas

```bash
python paxos.py --port 9101 --replica-id 1
python paxos.py --port 9102 --replica-id 2
python paxos.py --port 9103 --replica-id 3
```

### Terminal 10 — DFS demo client (Part 1)

```bash
python dfs_part_a.py --host 127.0.0.1 --port 9000 --demo
```

This demo will:

- create and append a DFS text file
- read, head, and tail that file
- create an input record file
- run `sort_file(input, output)`
- print the sorted DFS output

## Part 2 demo — Replication + Paxos + Failure scenarios

After starting all 9 infrastructure terminals above, use Terminal 10 to run the
Part 2 failure demo.

### Run all failure scenarios at once

```bash
python demo_failure.py
```

### Run individual scenarios

```bash
python demo_failure.py --scenario crash       # follower crash during replication
python demo_failure.py --scenario delay       # delayed ACCEPT message
python demo_failure.py --scenario retransmit  # retransmitted ACCEPT (idempotency)
python demo_failure.py --scenario sort        # distributed sort with 100 records
```

### What each scenario shows

**crash** — Follower crash during replication
- 3 Paxos replicas are alive for slot 1
- After slot 1, replica 3 is treated as crashed and skipped
- Leader commits using only replicas 1 and 2 (majority of 3)
- System remains fully available

**delay** — Delayed ACCEPT message
- Replica 2 receives its ACCEPT after a 2-second delay
- Leader waits; majority is still reached once replica 2 responds
- Models a slow network path or temporarily overloaded node

**retransmit** — Retransmitted ACCEPT
- Leader sends the same ACCEPT twice to replica 1
- Replica accepts it both times (idempotent behavior)
- Models what happens when the leader suspects a lost message

**sort** — Distributed sort with 100 records
- Generates 100 random key,value records
- Stores them as a DFS file
- Runs sort_file() across Chord buckets
- Validates the output is globally sorted

### Paxos log format

Every Paxos message is printed in a human-readable format:

```
[leader]   → ACCEPT(ballot=1, slot=1) to replica=1
[leader]   ← ACK from replica=1
[leader]   → LEARN(ballot=1, slot=1) to replica=1
[leader]   ← LEARNED by replica=1
[leader] ✓ COMMIT slot=1 ballot=1 op=put key=metadata:hello.txt
[leader]   ✗ replica=3 is CRASHED — skipping
```

Replica terminals print their own view:

```
[replica 2] ACCEPT slot=1 ballot=1 op=put key=metadata:hello.txt
[replica 2] LEARN  slot=1 ballot=1 op=put key=metadata:hello.txt
```

## Example ad hoc commands

```bash
python dfs_part_a.py --touch hello.txt
python dfs_part_a.py --append hello.txt localfile.txt
python dfs_part_a.py --read hello.txt
python dfs_part_a.py --head hello.txt 5
python dfs_part_a.py --tail hello.txt 5
python dfs_part_a.py --stat hello.txt
python dfs_part_a.py --ls
python dfs_part_a.py --sort-file records.txt records.sorted.txt
python dfs_part_a.py --delete-file hello.txt
```

## Distributed sort design

- Each input record has the form `key,value`.
- The DFS client reads all pages of the input file.
- Each record is mapped to a bucket owner using the Chord successor of `sort-key:<key>`.
- Records destined for the same owner are stored in a local sorted bucket object.
- All bucket contents are merged into one globally sorted list.
- The final sorted contents are written as a new DFS file.
- A sort manifest is stored in the output file metadata for debugging.

## Fault model assumptions

- Crash failures only — nodes stop, they do not behave maliciously
- No Byzantine failures
- Messages may be delayed, lost, duplicated, or reordered
- A majority of Paxos replicas (at least 2 of 3) remain alive
- Chord nodes may leave; the ring stabilizes over time

## Limitations

- Paxos is simplified single-leader; not full Multi-Paxos
- No persistent disk-backed storage — state lives in memory only
- No automatic replica repair after a node recovers
- No dynamic load rebalancing beyond Chord routing
- Leader is deterministic and fixed; no leader election on leader crash

## What to show in your report

- How Chord routes metadata and pages
- Why DFS objects are replicated three ways
- How the Paxos message flow orders updates
- How sorting partitions records by key and assembles the final sorted output
- Replication strategy and replica placement
- Failure assumptions and how the system handles them
- Limitations: no disk recovery, no full rebalancing on node departure, simplified leader model