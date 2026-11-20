"""Custom persistent page files, page cache, and row/column layouts.

No third-party database engine is used. The page format is deliberately simple:
magic(4) + number_of_records(uint32) + [payload_size(uint32), JSON bytes]*.
Rows are immutable in this prototype; transactions, deletion and recovery are not implemented.
"""
from __future__ import annotations

import json
import os
import struct
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

MAGIC = b"MDP1"
PAGE_SIZE = 4096
HEADER_SIZE = 8
UINT32 = struct.Struct("<I")


def encode_record(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class RecordID:
    page: int
    slot: int

    def to_list(self) -> list[int]:
        return [self.page, self.slot]

    @classmethod
    def from_list(cls, value: list[int]) -> "RecordID":
        return cls(*value)


class PageFile:
    """Fixed-size pages at byte offsets page_id * page_size."""

    def __init__(self, path: str | Path, page_size: int = PAGE_SIZE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.page_size = page_size
        if self.page_size < 128:
            raise ValueError("page size must be >= 128")
        self._file = self.path.open("r+b" if self.path.exists() else "w+b")
        self._file.seek(0, os.SEEK_END)
        if self._file.tell() % page_size:
            raise ValueError("corrupt page file: incomplete last page")

    @property
    def page_count(self) -> int:
        self._file.seek(0, os.SEEK_END)
        return self._file.tell() // self.page_size

    def allocate(self) -> int:
        page_id = self.page_count
        page = bytearray(self.page_size)
        page[:4] = MAGIC
        UINT32.pack_into(page, 4, 0)
        self.write(page_id, page)
        return page_id

    def read(self, page_id: int) -> bytearray:
        if not 0 <= page_id < self.page_count:
            raise IndexError("page out of range")
        self._file.seek(page_id * self.page_size)
        data = self._file.read(self.page_size)
        if len(data) != self.page_size or data[:4] != MAGIC:
            raise ValueError("corrupt page header")
        return bytearray(data)

    def write(self, page_id: int, data: bytes | bytearray) -> None:
        if len(data) != self.page_size or data[:4] != MAGIC:
            raise ValueError("invalid page")
        if not 0 <= page_id <= self.page_count:
            raise IndexError("cannot leave holes in page file")
        self._file.seek(page_id * self.page_size)
        self._file.write(data)
        self._file.flush()

    def sync(self) -> None:
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        self.sync()
        self._file.close()


class BufferPool:
    """Capacity-limited write-back cache with configurable FIFO or LRU eviction."""

    def __init__(self, page_file: PageFile, capacity: int = 8, policy: str = "LRU"):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        policy = policy.upper()
        if policy not in {"FIFO", "LRU"}:
            raise ValueError("policy must be FIFO or LRU")
        self.file = page_file
        self.capacity = capacity
        self.policy = policy
        self.cache: OrderedDict[int, tuple[bytearray, bool]] = OrderedDict()
        self.stats = {k: 0 for k in ("hits", "misses", "disk_reads", "disk_writes", "evictions")}

    def get(self, page_id: int) -> bytearray:
        if page_id in self.cache:
            self.stats["hits"] += 1
            if self.policy == "LRU":
                self.cache.move_to_end(page_id)
            return self.cache[page_id][0]
        self.stats["misses"] += 1
        if len(self.cache) == self.capacity:
            old_id, (old_page, dirty) = self.cache.popitem(last=False)
            self.stats["evictions"] += 1
            if dirty:
                self.file.write(old_id, old_page)
                self.stats["disk_writes"] += 1
        data = self.file.read(page_id)
        self.stats["disk_reads"] += 1
        self.cache[page_id] = (data, False)
        return data

    def mark_dirty(self, page_id: int) -> None:
        if page_id not in self.cache:
            raise KeyError("cannot dirty an uncached page")
        page, _ = self.cache[page_id]
        self.cache[page_id] = (page, True)

    def flush(self) -> None:
        for page_id, (page, dirty) in list(self.cache.items()):
            if dirty:
                self.file.write(page_id, page)
                self.stats["disk_writes"] += 1
                self.cache[page_id] = (page, False)
        self.file.sync()

    def reset_stats(self) -> None:
        self.stats = {key: 0 for key in self.stats}

    @property
    def hit_ratio(self) -> float:
        total = self.stats["hits"] + self.stats["misses"]
        return self.stats["hits"] / total if total else 0.0


class RowStore:
    """Append-only slotted record pages; each record is a length-prefixed JSON row."""

    def __init__(self, path: str | Path, capacity: int = 8, policy: str = "LRU", page_size: int = PAGE_SIZE):
        self.page_file = PageFile(path, page_size)
        self.buffer = BufferPool(self.page_file, capacity, policy)
        self.page_size = page_size

    def _slots(self, page: bytearray) -> list[tuple[int, int]]:
        count = UINT32.unpack_from(page, 4)[0]
        offset = HEADER_SIZE
        slots = []
        for _ in range(count):
            if offset + 4 > self.page_size:
                raise ValueError("corrupt record count")
            length = UINT32.unpack_from(page, offset)[0]
            offset += 4
            if offset + length > self.page_size:
                raise ValueError("corrupt record length")
            slots.append((offset, length))
            offset += length
        return slots

    def insert(self, row: dict) -> RecordID:
        payload = encode_record(row)
        if len(payload) + HEADER_SIZE + 4 > self.page_size:
            raise ValueError("record exceeds page size")
        if self.page_file.page_count == 0:
            self.page_file.allocate()
        page_id = self.page_file.page_count - 1
        page = self.buffer.get(page_id)
        slots = self._slots(page)
        free = HEADER_SIZE if not slots else slots[-1][0] + slots[-1][1]
        if free + 4 + len(payload) > self.page_size:
            self.buffer.flush()
            page_id = self.page_file.allocate()
            page = self.buffer.get(page_id)
            slots = []
            free = HEADER_SIZE
        UINT32.pack_into(page, free, len(payload))
        page[free + 4:free + 4 + len(payload)] = payload
        UINT32.pack_into(page, 4, len(slots) + 1)
        self.buffer.mark_dirty(page_id)
        return RecordID(page_id, len(slots))

    def get(self, rid: RecordID) -> dict:
        page = self.buffer.get(rid.page)
        slots = self._slots(page)
        if not 0 <= rid.slot < len(slots):
            raise IndexError("slot out of range")
        offset, length = slots[rid.slot]
        return json.loads(page[offset:offset + length])

    def scan(self) -> Iterator[tuple[RecordID, dict]]:
        for page_id in range(self.page_file.page_count):
            page = self.buffer.get(page_id)
            for slot, (offset, length) in enumerate(self._slots(page)):
                yield RecordID(page_id, slot), json.loads(page[offset:offset + length])

    def flush(self) -> None:
        self.buffer.flush()

    def close(self) -> None:
        self.flush()
        self.page_file.close()


class ColumnStore:
    """Columnar physical layout: each column is in a separate persistent page file."""

    def __init__(self, directory: str | Path, columns: list[str], capacity: int = 8, policy: str = "LRU"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.columns = tuple(columns)
        if not all(col.isidentifier() for col in columns):
            raise ValueError("invalid column name")
        self.stores = {
            col: RowStore(self.directory / f"{col}.pages", capacity, policy)
            for col in columns
        }

    def insert(self, row: dict) -> None:
        for col in self.columns:
            self.stores[col].insert({"v": row.get(col)})

    def scan(self, columns: list[str] | None = None) -> Iterator[dict]:
        selected = list(self.columns) if columns is None else columns
        if any(col not in self.stores for col in selected):
            raise KeyError("unknown column")
        if not selected:
            return
        streams = [self.stores[col].scan() for col in selected]
        from itertools import zip_longest
        for cells in zip_longest(*streams):
            if any(cell is None for cell in cells):
                raise ValueError("misaligned column lengths")
            yield {col: cell[1]["v"] for col, cell in zip(selected, cells)}

    def metrics(self) -> dict:
        return {col: {**store.buffer.stats, "hit_ratio": store.buffer.hit_ratio}
                for col, store in self.stores.items()}

    def flush(self) -> None:
        for store in self.stores.values():
            store.flush()

    def close(self) -> None:
        for store in self.stores.values():
            store.close()
