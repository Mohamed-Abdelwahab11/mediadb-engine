"""Reproducible storage and indexing experiments; standard-library-only benchmarking.

Examples:
  python scripts/benchmark.py --records 600 --runs 5 --out results
  python scripts/benchmark.py --records 2000 --runs 5 --out results

All outputs are actual measurements on the machine where this script is run.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import random
import statistics
import sys
import tempfile
from pathlib import Path
from time import perf_counter_ns

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mediadb.database import MediaDatabase
from mediadb.indexes import BPlusTree, LSMTree
from mediadb.storage import BufferPool, ColumnStore, PageFile, RecordID, RowStore

SEED = 302


def media(i: int) -> dict:
    return {
        "media_id": f"M{i:07d}", "title": f"Synthetic video asset {i}: sample metadata",
        "media_type": ["video", "image", "audio"][i % 3],
        "category": ["education", "science", "news", "art"][i % 4],
        "creator": f"Creator{i % 40:02d}", "size_bytes": 1000 + i * 31,
        "duration_seconds": float(i % 3600), "created_at": f"2026-11-{(i % 28) + 1:02d}"
    }


def timing_ms(fn) -> float:
    start = perf_counter_ns()
    fn()
    return (perf_counter_ns() - start) / 1e6


def benchmark(records: int, runs: int, out: Path):
    if records < 100 or runs < 1:
        raise ValueError("minimum 100 records and 1 measured run")
    out.mkdir(parents=True, exist_ok=True)
    trials: list[dict] = []
    def record(label: str, trial: int, elapsed: float, **extra):
        trials.append({"experiment": label, "run": trial, "elapsed_ms": round(elapsed, 4), **extra})

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        rows_path = root / "bench_row.pages"
        cols_path = root / "bench_columns"
        rowstore = RowStore(rows_path, capacity=8)
        columns = ColumnStore(cols_path, list(media(0)), capacity=8)
        data = [media(i) for i in range(records)]
        for row in data:
            rowstore.insert(row)
            columns.insert(row)
        rowstore.flush()
        columns.flush()

        # Physical layouts: same predicate, projected one column from two physical layouts.
        def scan_row():
            return sum(1 for _, row in rowstore.scan() if row["media_type"] == "video")
        def scan_col():
            return sum(1 for row in columns.scan(["media_type"]) if row["media_type"] == "video")
        assert scan_row() == scan_col()
        for trial in range(runs):
            rowstore.buffer.reset_stats()
            elapsed = timing_ms(scan_row)
            record("storage_row_scan_media_type", trial + 1, elapsed, **rowstore.buffer.stats)
            columns.stores["media_type"].buffer.reset_stats()
            elapsed = timing_ms(scan_col)
            record("storage_column_scan_media_type", trial + 1, elapsed, **columns.stores["media_type"].buffer.stats)
        rowstore.close()
        columns.close()

        # Buffer eviction policies: exactly the same deterministic page reference stream.
        pagefile = PageFile(root / "policy.pages")
        for _ in range(50):
            pagefile.allocate()
        rng = random.Random(SEED)
        refs = [rng.choice(list(range(7)) if rng.random() < 0.85 else list(range(7, 50))) for _ in range(500)]
        for policy in ("FIFO", "LRU"):
            for trial in range(runs):
                cache = BufferPool(pagefile, capacity=5, policy=policy)
                elapsed = timing_ms(lambda: [cache.get(ref) for ref in refs])
                record(f"cache_{policy}", trial + 1, elapsed, **cache.stats,
                       hit_ratio=round(cache.hit_ratio, 5))
        pagefile.close()

        # Indexed lookup versus page-based full scan. Results must be identical.
        dbpath = root / "db"
        with MediaDatabase(dbpath, cache_pages=8) as db:
            for row in data:
                db.insert(row)
            db.create_index("media_id", "bplus")
            rng = random.Random(SEED)
            targets = [f"M{i:07d}" for i in rng.sample(range(records), min(40, records))]
            def indexed():
                return [db.select("media_id", target) for target in targets]
            def fullscan():
                return [db.select("media_id", target, use_index=False) for target in targets]
            assert indexed() == fullscan()
            for trial in range(runs):
                db.rows.buffer.reset_stats()
                record("index_equality", trial + 1, timing_ms(indexed), **db.rows.buffer.stats)
                db.rows.buffer.reset_stats()
                record("scan_equality", trial + 1, timing_ms(fullscan), **db.rows.buffer.stats)

        # Isolated index insertion/build costs and read-heavy vs write-heavy operations.
        # Both indexes receive identical key/RID pairs; storage cost is excluded explicitly.
        ids = list(range(records))
        rng = random.Random(SEED)
        rng.shuffle(ids)
        for kind in ("bplus", "lsm"):
            for trial in range(runs):
                if kind == "bplus":
                    tree = BPlusTree(order=8)
                else:
                    tree = LSMTree(root / f"lsm_{trial}", threshold=64, max_runs=4)
                    # remove prior run content so trials have identical starting states
                    for path in tree.runs:
                        path.unlink()
                    tree = LSMTree(root / f"lsm_{trial}", threshold=64, max_runs=4)
                elapsed = timing_ms(lambda: [tree.insert(i, RecordID(i, 0)) for i in ids])
                if kind == "lsm":
                    elapsed += timing_ms(tree.flush)
                record(f"index_{kind}_build", trial + 1, elapsed,
                       index_bytes=(tree.disk_bytes if kind == "lsm" else 0),
                       node_count=(tree.node_count() if kind == "bplus" else 0))
                if kind == "bplus":
                    tree_lookup = tree.lookup
                else:
                    tree_lookup = tree.lookup
                targets = [ids[i % len(ids)] for i in range(100)]
                assert all(tree_lookup(target) == [RecordID(target, 0)] for target in targets)
                record(f"index_{kind}_read_heavy", trial + 1,
                       timing_ms(lambda: [tree_lookup(target) for target in targets]))

    # Aggregates and measured raw data: no hypothetical benchmark numbers.
    groups = sorted({trial["experiment"] for trial in trials})
    summary = {}
    for group in groups:
        values = [t["elapsed_ms"] for t in trials if t["experiment"] == group]
        ordered = sorted(values)
        summary[group] = {
            "runs": len(values), "mean_ms": round(statistics.mean(values), 4),
            "median_ms": round(statistics.median(values), 4),
            "p95_ms": round(ordered[max(0, math.ceil(0.95 * len(values)) - 1)], 4),
            "min_ms": min(values), "max_ms": max(values),
            "stdev_ms": round(statistics.stdev(values) if len(values) > 1 else 0.0, 4),
        }
    info = {
        "machine": {"system": platform.system(), "release": platform.release(),
                    "architecture": platform.machine(), "processor": platform.processor(),
                    "python": platform.python_version()},
        "configuration": {"seed": SEED, "records": records, "measured_runs": runs,
                          "cache_pages": 8, "policy_test_pages": 50, "policy_cache_pages": 5,
                          "policy_reference_count": 500, "concurrency": 1,
                          "workload": "deterministic synthetic media metadata"},
        "results": summary,
    }
    (out / "summary.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    keys = sorted({key for trial in trials for key in trial})
    with (out / "raw_trials.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=keys)
        writer.writeheader()
        writer.writerows(trials)

    # Standalone SVG chart with data labels, no plotting library required.
    W, H, left, top, height = 1050, 520, 250, 45, 410
    mx = max(x["mean_ms"] for x in summary.values()) or 1.0
    bar_h = max(18, int(height / len(groups)) - 7)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<text x="30" y="24" font-family="Arial" font-size="18" font-weight="bold">MediaDB measured benchmark (mean ms)</text>']
    for i, group in enumerate(groups):
        y = top + i * (height / len(groups))
        width = 650 * summary[group]["mean_ms"] / mx
        parts += [f'<text x="10" y="{y+bar_h*0.75:.1f}" font-size="12" font-family="Arial">{group}</text>',
                  f'<rect x="{left}" y="{y:.1f}" width="{width:.1f}" height="{bar_h}" fill="#245b88"/>',
                  f'<text x="{left+width+8:.1f}" y="{y+bar_h*0.75:.1f}" font-size="12" font-family="Arial">{summary[group]["mean_ms"]:.3f} ms</text>']
    parts.append('</svg>')
    (out / "benchmark_chart.svg").write_text("\n".join(parts), encoding="utf-8")
    print("Wrote", out.resolve())
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=int, default=600)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--out", type=Path, default=Path("results"))
    args = parser.parse_args()
    benchmark(args.records, args.runs, args.out)
