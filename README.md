# Distributed File System — Part A

## Overview

This project implements a Distributed File System (DFS) on top of a Chord-based Distributed Hash Table (DHT). Files are split into pages and stored across multiple nodes in a Chord ring. The DFS supports basic file operations like creating, reading, appending, and deleting files.

---

## Files

- `chord.py` — Chord DHT node implementation (provided, slightly modified)
- `dfs_part_a.py` — DFS implementation
- `README.md` — This file

---

## Requirements

- Python 3.11+
- Pyro5

To install:

```
python3 -m venv venv
source venv/bin/activate
pip3 install Pyro5
```

---

## How to Run

Open 7 terminals. Run `source venv/bin/activate` in each one first.

**Terminal 1:**
```
python3 -m Pyro5.nameserver
```

**Terminal 2:**
```
python3 chord.py node --port 9000 --bootstrap
```

**Terminals 3-6:**
```
python3 chord.py node --port 9001 --join 127.0.0.1:9000
python3 chord.py node --port 9002 --join 127.0.0.1:9000
python3 chord.py node --port 9003 --join 127.0.0.1:9000
python3 chord.py node --port 9004 --join 127.0.0.1:9000
```

**Terminal 7:**
```
python3 dfs_part_a.py
```

---

## Supported Operations

- `touch(filename)` — creates a new empty file
- `append(filename, local_path)` — appends a local file's contents into the DFS
- `read(filename)` — returns the full contents of a file
- `head(filename, n)` — returns the first n lines
- `tail(filename, n)` — returns the last n lines
- `delete_file(filename)` — deletes a file and all its pages
- `ls()` — lists all files
- `stat(filename)` — returns file metadata (size, pages, version)

---

## How It Works

Files are broken into 4096-byte pages. Each page is stored on a Chord node determined by hashing the page key. Metadata (filename, size, page list) is stored separately and also placed in the ring by hashing the metadata key.

When reading a file, the DFS looks up the metadata first, then fetches each page in order and reassembles the content.

Keys are plain strings like `"metadata:hello.txt"` and `"hello.txt:0"`. These get hashed to find which Chord node is responsible for storing them.

---

## Changes to chord.py

Three methods were added to `ChordNode` to allow the DFS to store data on nodes:

- `store_put(key, value)` — saves a key-value pair locally on the node
- `store_get(key)` — retrieves a value by key
- `store_delete(key)` — deletes a key

Everything else in `chord.py` is unchanged from the provided starter code.

---

## Libraries Used

- Pyro5 — lets nodes communicate over the network
- hashlib, base64, json, os — Python standard library