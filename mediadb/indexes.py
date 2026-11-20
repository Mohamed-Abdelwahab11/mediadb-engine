"""B+ tree and log-structured merge tree indexes."""
from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from pathlib import Path
from .storage import RecordID


@dataclass
class Leaf:
    keys: list = field(default_factory=list)
    postings: list[list[RecordID]] = field(default_factory=list)
    next: "Leaf | None" = None


@dataclass
class Internal:
    keys: list = field(default_factory=list)
    children: list = field(default_factory=list)


class BPlusTree:
    """B+ tree: sorted leaf entries, linked leaves, node splitting on overflow."""

    def __init__(self, order: int = 8):
        if order < 3:
            raise ValueError("order must be >= 3")
        self.order = order  # maximum number of keys in a node
        self.root: Leaf | Internal = Leaf()

    def insert(self, key, rid: RecordID):
        split = self._insert(self.root, key, rid)
        if split:
            separator, right = split
            self.root = Internal([separator], [self.root, right])

    def _insert(self, node, key, rid):
        if isinstance(node, Leaf):
            pos = bisect.bisect_left(node.keys, key)
            if pos < len(node.keys) and node.keys[pos] == key:
                node.postings[pos].append(rid)
                return None
            node.keys.insert(pos, key)
            node.postings.insert(pos, [rid])
            if len(node.keys) <= self.order:
                return None
            mid = (len(node.keys) + 1) // 2
            right = Leaf(node.keys[mid:], node.postings[mid:], node.next)
            node.keys = node.keys[:mid]
            node.postings = node.postings[:mid]
            node.next = right
            return right.keys[0], right
        pos = bisect.bisect_right(node.keys, key)
        split = self._insert(node.children[pos], key, rid)
        if split is None:
            return None
        separator, right = split
        node.keys.insert(pos, separator)
        node.children.insert(pos + 1, right)
        if len(node.keys) <= self.order:
            return None
        mid = len(node.keys) // 2
        promote = node.keys[mid]
        new_right = Internal(node.keys[mid + 1:], node.children[mid + 1:])
        node.keys = node.keys[:mid]
        node.children = node.children[:mid + 1]
        return promote, new_right

    def _find_leaf(self, key):
        node = self.root
        while isinstance(node, Internal):
            node = node.children[bisect.bisect_right(node.keys, key)]
        return node

    def lookup(self, key) -> list[RecordID]:
        leaf = self._find_leaf(key)
        pos = bisect.bisect_left(leaf.keys, key)
        return list(leaf.postings[pos]) if pos < len(leaf.keys) and leaf.keys[pos] == key else []

    def range(self, low, high) -> list[RecordID]:
        if low > high:
            return []
        leaf = self._find_leaf(low)
        result = []
        while leaf is not None:
            for key, postings in zip(leaf.keys, leaf.postings):
                if key > high:
                    return result
                if key >= low:
                    result.extend(postings)
            leaf = leaf.next
        return result

    def node_count(self) -> int:
        def count(n):
            return 1 if isinstance(n, Leaf) else 1 + sum(count(c) for c in n.children)
        return count(self.root)


class LSMTree:
    """Write-optimized memtable + durable sorted SSTable runs + merge compaction.

    Tombstones, updates, transactions and write-ahead logging are not implemented.
    """

    def __init__(self, directory: str | Path, threshold: int = 32, max_runs: int = 4):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        if threshold < 1 or max_runs < 2:
            raise ValueError("invalid LSM configuration")
        self.threshold = threshold
        self.max_runs = max_runs
        self.memtable: dict = {}
        self.runs = sorted(self.directory.glob("sst_*.json"))
        self.next_id = max((int(p.stem.split("_")[-1]) for p in self.runs), default=-1) + 1

    @staticmethod
    def _pack(mapping: dict) -> list:
        return [[key, [rid.to_list() for rid in postings]] for key, postings in sorted(mapping.items())]

    @staticmethod
    def _unpack(data: list) -> dict:
        return {key: [RecordID.from_list(x) for x in ids] for key, ids in data}

    def insert(self, key, rid: RecordID) -> None:
        self.memtable.setdefault(key, []).append(rid)
        if len(self.memtable) >= self.threshold:
            self.flush()

    def _write_run(self, entries: dict) -> Path:
        path = self.directory / f"sst_{self.next_id:06d}.json"
        self.next_id += 1
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._pack(entries), separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
        return path

    def _load_run(self, path: Path) -> dict:
        return self._unpack(json.loads(path.read_text(encoding="utf-8")))

    def flush(self) -> None:
        if self.memtable:
            self.runs.append(self._write_run(self.memtable))
            self.memtable = {}
        if len(self.runs) > self.max_runs:
            self.compact()

    def compact(self) -> None:
        merged = {}
        for path in self.runs:
            for key, ids in self._load_run(path).items():
                merged.setdefault(key, []).extend(ids)
        old_runs = self.runs
        self.runs = [self._write_run(merged)]
        for path in old_runs:
            path.unlink()

    def lookup(self, key) -> list[RecordID]:
        result = list(self.memtable.get(key, []))
        for path in reversed(self.runs):
            data = self._load_run(path)
            result.extend(data.get(key, []))
        return result

    def range(self, low, high) -> list[RecordID]:
        if low > high:
            return []
        result = []
        sources = [self.memtable] + [self._load_run(path) for path in reversed(self.runs)]
        for source in sources:
            for key, ids in source.items():
                if low <= key <= high:
                    result.extend(ids)
        return result

    @property
    def disk_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.runs)


def choose_index(read_fraction: float) -> str:
    """Documented heuristic: >=60% lookups selects B+; otherwise LSM."""
    if not 0 <= read_fraction <= 1:
        raise ValueError("read_fraction must be within [0, 1]")
    return "bplus" if read_fraction >= 0.60 else "lsm"
