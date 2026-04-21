
## Terminal 10 Output


## Address:

## Chord integration : 

Chord is deemed crucial in this project since it determines ownership of file pages and sorted bucket objects. Each node is assigned a GUID using consistent hashing. File pages and sort buckets are also assigned deterministic keys, such as: 

hello.txt : 0
records.txt:0
sorted-bucket:records.sorted.txt:122
sorted-bucket:records.sorted.txt:225

The object key is hashed and routed to the responsible successor node using standard chord successor lookup. 

An example being:

[node 122] registered chord.node.122 -> PYRO:obj_89ce4d92cfb642c1b7bbe15ec7c5d063@127.0.0.1:9001
[node 122] joined via 165; successor=165
[node 122] running; ctrl-c to stop

This displays a node with identifier 122 which joins an existing ring through node 165 , which in turn learns it successor. This design allows the system to place page keys and sorted-bucket keys onto the correct storage node without a centralized directory. 


## Metadata and page design: 

The way we represent each file in this project is by using a metadata object plus one or more page objects. 

Metadata object is stored under keys like: 

metadata:hello.txt
metadata:records.txt
Metadata:records.sorted.txt

The metadata tracks file structure and versioning information. From the stat output for hello.txt:


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

This design shows that metadata stores:
filename
object type
total byte size
number of pages
page number and page GUID
replica identifiers for each page
metadata version

However, the actual file contents are stored in page objects such as: 
hello.txt:0
records.txt:0
Records.sorted.txt:0
It’s important to highlight separation since appending data updates the page object and then commits a new version of the metadata.  As seen in our terminal log: 
[leader] COMMIT slot=3 ballot=3 op=put key=hello.txt:0
[leader] COMMIT slot=4 ballot=4 op=put key=metadata:hello.txt
The first commit writes the page contents, and the second commit updates the file metadata.
The advantage of this design is that metadata changes are small and easy to replicate, scaling is more natural when it comes to larger files, append operations do not require rewriting all file state, and replication can be tracked per page.



## Distributed sorting strategy: 

The “sort()” operation uses a bucket-based distributed sorting design. Before the system sorts anything, it needs to commit metadata and page data for records.txt, that looks something like this : 

[leader] COMMIT slot=5 ballot=5 op=put key=metadata:records.txt
[leader] COMMIT slot=6 ballot=6 op=put key=dfs:index
[leader] COMMIT slot=7 ballot=7 op=put key=records.txt:0
[leader] COMMIT slot=8 ballot=8 op=put key=metadata:records.txt

As the system continues, it must partition records into buckets, which is based on the record key. The result was : 

{
"buckets": {
"225": 3,
"122": 2
},
"records": 5,
"output": "records.sorted.txt"
}

Now although the code itself can be a bit vague this simply means 5 total records were partitioned into two buckets, with 3 records routed to bucket 225 and 2 records routed to bucket 122. With this in mind each bucket is stored and written as a separate distributed object: 

[leader] COMMIT slot=9 ballot=9 op=put key=sorted-bucket:records.sorted.txt:122
[leader] COMMIT slot=10 ballot=10 op=put key=sorted-bucket:records.sorted.txt:225

Note that this is where the distributed sorting happens since each bucket can be sorted independently, potentially on different chord nodes. So once the bucket results are ready, the system commits metadata and output pages for the merged sorted file: 

[leader] COMMIT slot=11 ballot=11 op=put key=metadata:records.sorted.txt
[leader] COMMIT slot=12 ballot=12 op=put key=dfs:index
[leader] COMMIT slot=13 ballot=13 op=put key=records.sorted.txt:0
[leader] COMMIT slot=14 ballot=14 op=put key=metadata:records.sorted.txt
[leader] COMMIT slot=15 ballot=15 op=put key=metadata:records.sorted.txt

And once sorted looks like:

0005,zoe
0012,alice
0042,bob
0042,bobby
0190,carol

## Replication strategy:

 In our project, we decided to replicate each page three times instead of storing only a single copy. We did this because if one node crashes, we still want the file to remain available and readable from another location.
This can be seen directly in the stat output for hello.txt:

"replicas": [
"hello.txt:0|replica|0",
"hello.txt:0|replica|1",
"hello.txt:0|replica|2"
]
Our approach was:
the original page is stored using its normal GUID, such as hello.txt:0
three replica identifiers are created for backup copies
the metadata keeps track of all replica locations
This made our design easier because we could manage replication at the page level instead of copying the full file every time. Since metadata already stores page information, adding replica tracking fit naturally into the system.
Why we chose this strategy:
We wanted a simple but reliable fault-tolerance model. If a node storing the main page fails, another replica can still serve the file. This helps prevent data loss and improves availability.
It also works well for both normal files and sorted output files because the same page-based logic applies everywhere.
What we learned:
One thing we noticed is that replication becomes much easier when metadata is separated from content pages. Instead of treating the whole file as one object, we can recover and manage individual pages more efficiently.
If we had more time, we would add automatic replica repair so the system could recreate missing replicas after a failure.
## Paxos Message Flow: 
For metadata consistency, we used Paxos so that all important updates happen in the same agreed order across replicas. We mainly used Paxos for operations like touch, append, and sort, where metadata must stay consistent even if one replica fails.

The leader process creates the commit first, and then the replicas respond with ACCEPT and LEARN messages.

Leader point of view: 
[leader] COMMIT slot=1 ballot=1 op=put key=metadata:hello.txt
[leader] COMMIT slot=2 ballot=2 op=put key=dfs:index
[leader] COMMIT slot=3 ballot=3 op=put key=hello.txt:0
[leader] COMMIT slot=4 ballot=4 op=put key=metadata:hello.txt

During the sorting: 
[leader] COMMIT slot=9 ballot=9 op=put key=sorted-bucket:records.sorted.txt:122
[leader] COMMIT slot=10 ballot=10 op=put key=sorted-bucket:records.sorted.txt:225
[leader] COMMIT slot=11 ballot=11 op=put key=metadata:records.sorted.txt

Replica point of view: (Replica 2 confirms the proposal was accepted and learned)

[replica 2] ACCEPT slot=1 ballot=1 op=put key=metadata:hello.txt
[replica 2] LEARN slot=1 ballot=1 op=put key=metadata:hello.txt

The process worked like this:
the leader proposes an operation using a slot and ballot number
replicas receive and accept that operation
once a majority agrees, the value is learned
the operation becomes part of the replicated metadata log
This helped us make sure that even if multiple file operations happened close together, every replica still saw the same final order.
## Failure Assumptions
While designing the system, we made several assumptions about failures so the project would stay realistic but still manageable.
We assumed:
nodes can crash, but they do not behave maliciously
there are no Byzantine failures
a majority of Paxos replicas stay alive
the network may be delayed, but messages are eventually delivered
Chord nodes can leave and later rejoin the ring
We focused mainly on crash failures because that is the most common case for a distributed storage system like this.
For example, if one storage node goes down, the replica copies still allow the file to be accessed. If one Paxos replica crashes, consensus can still continue as long as the majority remains available.
This assumption kept the design practical and allowed us to focus on correctness instead of defending against malicious behavior.
What this means for the system
The system is fault tolerant, but only up to a point. If too many Paxos replicas fail at once, consensus will stop. If all page replicas are lost, recovery is no longer possible.
Because of this, replication and majority voting are both necessary parts of the design.
## Limitations and Future Improvements
Even though the system worked successfully, there are still several limitations in our current design.
Current Limitations
First, our Paxos implementation is simplified and behaves more like a single-leader version instead of a full Multi-Paxos system. It works well for this project, but it is not fully optimized for larger production systems.
Second, Chord stabilization is limited. If nodes frequently join or leave, the system does not automatically repair all successor relationships or rebalance data as aggressively as a full implementation would.
Third, replica recovery is mostly manual. We store replica information correctly, but the system does not automatically rebuild missing replicas after a failure.
We also do not use persistent disk-backed storage, so most of the design is focused on correctness and architecture rather than full long-term durability.
Future Improvements
If I were continuing this project, I would improve several areas.
implement full Multi-Paxos optimization
add automatic failure detection for nodes
create background replica healing and recovery
improve dynamic Chord rebalancing after joins and leaves
support stronger concurrency handling for multiple users
add persistent distributed storage instead of temporary memory-based state
Overall, the project demonstrates the main distributed systems concepts successfully, but these improvements would improve our distributed file system on a larger scale.


## Test evidence: 
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
