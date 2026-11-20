# Architecture & Design

MediaDB is a compact experimental DBMS for persistent media-asset metadata and indexed queries. The media domain is a practical workload for storage and search; it does not imply a user-facing editor or media streaming platform.

## Record model

The current data set uses a single media record collection with fields such as `media_id`, `title`, `media_type`, `category`, `creator`, `size_bytes`, `duration_seconds`, and `created_at`. Samples are generated and contain no real personal information. Current field behavior is defined in `mediadb/database.py`.

## Physical page storage

`PageFile` manages 4,096-byte pages. Each page contains a 4-byte `MDP1` magic header, a 4-byte unsigned little-endian record count, and repeated record entries with a 4-byte payload length and UTF-8 JSON bytes. A `RecordID(page, slot)` identifies a stored row.

`RowStore` persists complete records. `ColumnStore` persists individual attributes in separately paged files. `BufferPool` holds a bounded cache and exposes FIFO and LRU eviction policies, write-back, and hit/miss/page-I/O counters. The layout and policies are testable independent of the example domain.

## Indexing and lookup

`BPlusTree` maintains an in-memory B+ tree with equality and inclusive range traversal; leaves are linked for scans, and keys can have multiple postings. On process startup, B+ tree indexes are reconstructed from persisted record pages.

`LSMTree` writes key/posting entries via a memtable to durable sorted runs with compaction. The `choose_index` rule selects B+ tree when the expected read fraction is at least 0.60; otherwise it selects LSM. It is a documented heuristic, not a cost-based optimizer.

## Objects and encoding

`ObjectStore` stores opaque bytes by SHA-256 content hash. The codec interface allows future encoding or authenticated-encryption implementations. The shipped `IdentityCodec` makes no security promises. This is local object storage, not an S3 protocol implementation.

## Supported query surface

Examples of the limited SQL-like syntax:

```
SELECT * FROM media WHERE media_id = M002
SELECT * FROM media WHERE category = 'education'
SELECT * FROM media WHERE size_bytes BETWEEN 1000 AND 10000
```

An existing index can narrow the page reads; non-indexed queries use a full scan. There is no general SQL parser, arbitrary expression engine, or join optimizer.

## Consistency boundaries

- Pages and LSM files persist across clean process shutdown and restart.
- The B+ tree must be rebuilt at startup, so cold start has a cost.
- There is no update/delete, WAL, crash-consistent transaction manager, or process concurrency guarantee.
- Object-store changes and metadata changes are not committed together atomically.
- All code runs on a single node; clustering and failover remain out of scope.

## Possible future domain expansion

Additional relations such as `creators`, `collections`, `collection_items`, and `events` could support query joins, multi-record transaction workloads, and future similarity search. **These are design ideas, not implemented features.**
