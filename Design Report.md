
## Terminal 10 Output

##Address:

Chord integration

metadata and page design

distributed sorting strategy

replication strategy

Paxos message flow

failure assumptions

limitations and future improvements

##Test evidence: 
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

=== sort ===
[leader] COMMIT slot=5 ballot=5 op=put key=metadata:records.txt
[leader] COMMIT slot=6 ballot=6 op=put key=dfs:index
[leader] COMMIT slot=7 ballot=7 op=put key=records.txt:0
[leader] COMMIT slot=8 ballot=8 op=put key=metadata:records.txt
[leader] COMMIT slot=9 ballot=9 op=put key=sorted-bucket:records.sorted.txt:122
[leader] COMMIT slot=10 ballot=10 op=put key=sorted-bucket:records.sorted.txt:225
[leader] COMMIT slot=11 ballot=11 op=put key=metadata:records.sorted.txt
[leader] COMMIT slot=12 ballot=12 op=put key=dfs:index
[leader] COMMIT slot=13 ballot=13 op=put key=records.sorted.txt:0
[leader] COMMIT slot=14 ballot=14 op=put key=metadata:records.sorted.txt
[leader] COMMIT slot=15 ballot=15 op=put key=metadata:records.sorted.txt
{
  "buckets": {
    "225": 3,
    "122": 2
  },
  "records": 5,
  "output": "records.sorted.txt"
}
0005,zoe
0012,alice
0042,bob
0042,bobby
0190,carol


## Paxos Terminal output
(venv) PS C:\Users\dougd\OneDrive\Documents\GitHub\Distributed-File-system> python paxos.py --host 127.0.0.1 --port 9102 --replica-id 2
[replica 2] registered paxos.replica.2 -> PYRO:obj_5e601c35e5b246b7bc49e07141751c33@127.0.0.1:9102
[replica 2] ACCEPT slot=1 ballot=1 op=put key=metadata:hello.txt
[replica 2] LEARN slot=1 ballot=1 op=put key=metadata:hello.txt
[replica 2] ACCEPT slot=2 ballot=2 op=put key=dfs:index
[replica 2] LEARN slot=2 ballot=2 op=put key=dfs:index
[replica 2] ACCEPT slot=3 ballot=3 op=put key=hello.txt:0
[replica 2] LEARN slot=3 ballot=3 op=put key=hello.txt:0
[replica 2] ACCEPT slot=4 ballot=4 op=put key=metadata:hello.txt
[replica 2] LEARN slot=4 ballot=4 op=put key=metadata:hello.txt
[replica 2] ACCEPT slot=5 ballot=5 op=put key=metadata:records.txt
[replica 2] LEARN slot=5 ballot=5 op=put key=metadata:records.txt
[replica 2] ACCEPT slot=6 ballot=6 op=put key=dfs:index
[replica 2] LEARN slot=6 ballot=6 op=put key=dfs:index
[replica 2] ACCEPT slot=7 ballot=7 op=put key=records.txt:0
[replica 2] LEARN slot=7 ballot=7 op=put key=records.txt:0
[replica 2] ACCEPT slot=8 ballot=8 op=put key=metadata:records.txt
[replica 2] LEARN slot=8 ballot=8 op=put key=metadata:records.txt
[replica 2] ACCEPT slot=9 ballot=9 op=put key=sorted-bucket:records.sorted.txt:122
[replica 2] LEARN slot=9 ballot=9 op=put key=sorted-bucket:records.sorted.txt:122
[replica 2] ACCEPT slot=10 ballot=10 op=put key=sorted-bucket:records.sorted.txt:225
[replica 2] LEARN slot=10 ballot=10 op=put key=sorted-bucket:records.sorted.txt:225
[replica 2] ACCEPT slot=11 ballot=11 op=put key=metadata:records.sorted.txt
[replica 2] LEARN slot=11 ballot=11 op=put key=metadata:records.sorted.txt
[replica 2] ACCEPT slot=12 ballot=12 op=put key=dfs:index
[replica 2] LEARN slot=12 ballot=12 op=put key=dfs:index
[replica 2] ACCEPT slot=13 ballot=13 op=put key=records.sorted.txt:0
[replica 2] LEARN slot=13 ballot=13 op=put key=records.sorted.txt:0
[replica 2] ACCEPT slot=14 ballot=14 op=put key=metadata:records.sorted.txt
[replica 2] LEARN slot=14 ballot=14 op=put key=metadata:records.sorted.txt
[replica 2] ACCEPT slot=15 ballot=15 op=put key=metadata:records.sorted.txt
[replica 2] LEARN slot=15 ballot=15 op=put key=metadata:records.sorted.txt

## Chord Node Output
(venv) PS C:\Users\dougd\OneDrive\Documents\GitHub\Distributed-File-system> python chord.py node --host 127.0.0.1 --port 9001 --join 127.0.0.1:9000
[node 122] registered chord.node.122 -> PYRO:obj_89ce4d92cfb642c1b7bbe15ec7c5d063@127.0.0.1:9001
[node 122] joined via 165; successor=165
[node 122] running; ctrl-c to stop
