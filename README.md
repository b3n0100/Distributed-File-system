# Distributed File System over Chord with Simplified Paxos

## Overview

This project extends a Chord ring into a small distributed file system. It supports:

- distributed file metadata and page storage through Chord routing
- deterministic metadata and page keys
- three-way replicated DFS objects
- a simplified Paxos-style commit path for DFS updates
- distributed sorting of `key,value` records into a new DFS file

The implementation is intentionally small and demo-friendly. It is designed to satisfy the assignment structure rather than be production-grade.

## Files

- `chord.py` - Chord DHT node implementation with local storage methods
- `dfs_part_a.py` - DFS client and DFS logic, including `sort_file`
- `paxos.py` - simplified Paxos replica service
- `README.md` - setup and usage

## Requirements

- Python 3.11+
- Pyro5

Install:

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

### Simplified Paxos flow

For every DFS update:

1. leader chooses `(slot, ballot)`
2. leader sends `ACCEPT(ballot, slot, op)` to 3 Paxos replicas
3. replicas log the acceptance and respond
4. leader sends `LEARN(ballot, slot, op)` to replicas that accepted
5. once a majority has learned, the leader commits the DFS update to Chord

This mirrors the simplified assignment rule that operations are committed after a majority learn them, and operations are ordered by slot.

## How to run

Open 10 terminals total, or use a terminal multiplexer.

### Terminal 1 - Pyro name server

```bash
python3 -m Pyro5.nameserver
```

### Terminals 2 through 6 - Chord peers

```bash
python3 chord.py node --port 9000 --bootstrap
python3 chord.py node --port 9001 --join 127.0.0.1:9000
python3 chord.py node --port 9002 --join 127.0.0.1:9000
python3 chord.py node --port 9003 --join 127.0.0.1:9000
python3 chord.py node --port 9004 --join 127.0.0.1:9000
```

### Terminals 7 through 9 - Paxos replicas

```bash
python3 paxos.py --port 9101 --replica-id 1
python3 paxos.py --port 9102 --replica-id 2
python3 paxos.py --port 9103 --replica-id 3
```

### Terminal 10 - DFS demo client

```bash
python3 dfs_part_a.py --host 127.0.0.1 --port 9000 --demo
```

This demo will:

- create and append a DFS text file
- read, head, and tail that file
- create an input record file
- run `sort_file(input, output)`
- print the sorted DFS output

## Example ad hoc commands

```bash
python3 dfs_part_a.py --touch hello.txt
python3 dfs_part_a.py --append hello.txt localfile.txt
python3 dfs_part_a.py --read hello.txt
python3 dfs_part_a.py --head hello.txt 5
python3 dfs_part_a.py --tail hello.txt 5
python3 dfs_part_a.py --stat hello.txt
python3 dfs_part_a.py --ls
python3 dfs_part_a.py --sort-file records.txt records.sorted.txt
python3 dfs_part_a.py --delete-file hello.txt
```

## Distributed sort design

- Each input record has the form `key,value`.
- The DFS client reads all pages of the input file.
- Each record is mapped to a bucket owner using the Chord successor of `sort-key:<key>`.
- Records destined for the same owner are stored in a local sorted bucket object.
- All bucket contents are merged into one globally sorted list.
- The final sorted contents are written as a new DFS file.
- A sort manifest is stored in the output file metadata for debugging.

## What to show in your report

- How Chord routes metadata and pages
- Why DFS objects are replicated three ways
- How the Paxos message flow orders updates
- How sorting partitions records by key and assembles the final sorted output
- Limitations: no disk recovery, no full rebalancing on node departure, simplified leader model
