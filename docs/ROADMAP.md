# Engineering roadmap

This roadmap distinguishes the current working prototype from possible later extensions. Planned capabilities are not present in the current release.

| Area | Current status | Potential improvement |
| --- | --- | --- |
| Durable storage | Custom page files; row/column layout | Updates, deletes, page reclamation, schema evolution |
| Buffer cache | FIFO and LRU; I/O counters | More policies and smarter write-back |
| Indexes | B+ tree and disk-backed LSM | Persistent B+ nodes, sparse SSTable index, Bloom filters |
| Query | Minimal `SELECT` equality/range syntax | Safe parser, vectorized SELECT, JOINs and plan selection |
| Transactions | Not implemented | MVCC, locking, WAL, recovery and deadlock handling |
| Distribution | Not implemented | Sharding, replication, consensus and atomic cross-node commits |
| Vector similarity | Not implemented | Fixed-dimensional vector storage and exact top-k search |
| Reliability | Tests and reproducible microbenchmarks | Fault injection, large-scale evaluation, complete workflows |

## Priorities before production use

Durability testing under crashes, startup index rebuild cost, large-data compaction performance, schema migrations, concurrent access, and a declared security model.
