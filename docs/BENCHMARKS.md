# Benchmark methodology and measured results

The measurements below come from **one actual execution** of the included benchmark script. They are not predicted timings or universal performance claims. CPU model and RAM were not captured; do not use these numbers to describe an untested device.

## Reproduce the experiment

```bash
python3 -m unittest discover -s tests -v
python3 scripts/benchmark.py --records 600 --runs 5 --out results
```

## Execution environment

| Parameter | Value |
| --- | --- |
| OS | Linux 6.18.57 |
| Architecture | x86_64 |
| Python | 3.13.5 |
| Synthetic asset records | 600 |
| Deterministic seed | 302 |
| Recorded repeats | 5 |
| Page cache (layout/lookup) | 8 pages |
| FIFO/LRU experiment | 500 references, 5-page cache |
| Concurrency | 1 |

## Timings (wall-clock milliseconds)

| Experiment | Mean (ms) | Median (ms) | p95 (ms) | Std dev. (ms) |
| --- | ---: | ---: | ---: | ---: |
| `cache_FIFO` | 0.7806 | 0.6971 | 1.1477 | 0.2056 |
| `cache_LRU` | 0.6630 | 0.6610 | 0.6796 | 0.0144 |
| `index_bplus_build` | 0.6165 | 0.6318 | 0.7079 | 0.0749 |
| `index_bplus_read_heavy` | 0.0577 | 0.0436 | 0.1150 | 0.0320 |
| `index_equality` | 0.4385 | 0.4414 | 0.4702 | 0.0261 |
| `index_lsm_build` | 4.1902 | 4.2186 | 4.9498 | 0.5468 |
| `index_lsm_read_heavy` | 62.3196 | 49.1823 | 87.2941 | 22.5740 |
| `scan_equality` | 75.7420 | 75.4675 | 76.8101 | 0.8056 |
| `storage_column_scan_media_type` | 1.3383 | 1.3348 | 1.3600 | 0.0148 |
| `storage_row_scan_media_type` | 1.7655 | 1.7463 | 1.8620 | 0.0548 |

- **FIFO cache:** 223 hits, 277 misses, 277 page reads, 272 evictions; hit ratio 44.6% for this specific trace.
- **LRU cache:** 234 hits, 266 misses, 266 page reads, 261 evictions; hit ratio 46.8% for this specific trace.

## Interpretation and fairness caveats

- The row and column scenarios filter the same synthetic records; physical access differs because column projection reads one attribute.
- The indexed and full-scan scenarios return the same results; correctness is asserted before comparing lookup timing. The index-building phase is not included in indexed search latency.
- The B+ tree is an **in-memory** structure rebuilt from durable records after restarting; the LSM writes sorted runs to disk. Their build times do **not** offer a durability-equivalent comparison.
- The LSM implementation loads a full run for lookups, and lacks Bloom filters, sparse indexes, and block caching. Its read-heavy timings illustrate this prototype, **not** production LSM tree behavior.
- Page-cache results are specific to one deterministic access sequence. Neither FIFO nor LRU is universally superior.
- Five repeats provide a coarse p95 estimate; OS cache, Python overhead, background processes, and file systems affect timings. Larger data sets and additional repeats are recommended.
- The benchmark does not test crash recovery, transaction isolation, concurrent writes, distributed operation, or production-scale throughput.

## Evidence files

- [`raw_trials.csv`](../results/raw_trials.csv): raw timing records and page counters
- [`summary.json`](../results/summary.json): parameters, machine metadata and summary statistics
- [`benchmark_chart.svg`](../results/benchmark_chart.svg): summary visualization
- [`tests/`](../tests/): storage, index and integration correctness tests

## Current functional boundaries

The project supports durable append/read storage, two index structures, lightweight SQL-like filtering, and local binary objects with a codec hook. It does not currently support updates/deletes, transaction atomicity, crash recovery, joins, clustering, encryption, or semantic vector retrieval.
