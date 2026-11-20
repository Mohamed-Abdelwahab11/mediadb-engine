"""Integration boundary between persistent page storage and secondary indexes."""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from .indexes import BPlusTree, LSMTree, choose_index
from .objects import ObjectStore
from .storage import ColumnStore, RowStore

# A synthetic media domain is a workload only; no external DBMS underneath.
SCHEMA = {
    "media_id": str,
    "title": str,
    "media_type": str,
    "category": str,
    "creator": str,
    "size_bytes": int,
    "duration_seconds": float,
    "created_at": str,
}


class MediaDatabase:
    def __init__(self, directory: str | Path, cache_pages: int = 8, policy: str = "LRU", lsm_threshold: int = 32):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.root / "metadata.json"
        self.metadata = {"schema_version": 1, "indexes": {}}
        if self.meta_path.exists():
            self.metadata = json.loads(self.meta_path.read_text(encoding="utf-8"))
        self.rows = RowStore(self.root / "row_store.pages", capacity=cache_pages, policy=policy)
        self.columns = ColumnStore(self.root / "column_store", list(SCHEMA), capacity=cache_pages, policy=policy)
        self.objects = ObjectStore(self.root / "objects")
        self.lsm_threshold = lsm_threshold
        self.indexes: dict[str, BPlusTree | LSMTree] = {}
        for field, kind in self.metadata["indexes"].items():
            if kind == "bplus":
                tree = BPlusTree()
                for rid, row in self.rows.scan():
                    tree.insert(row[field], rid)
                self.indexes[field] = tree
            elif kind == "lsm":
                self.indexes[field] = LSMTree(self.root / "indexes" / field, threshold=lsm_threshold)
            else:
                raise ValueError("unrecognized index kind")

    def _write_metadata(self):
        temporary = self.meta_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.metadata, indent=2), encoding="utf-8")
        temporary.replace(self.meta_path)

    def insert(self, row: dict) -> dict:
        if set(row) != set(SCHEMA):
            raise ValueError(f"expected exactly these fields: {list(SCHEMA)}")
        for col, dtype in SCHEMA.items():
            if not isinstance(row[col], dtype):
                raise TypeError(f"{col} must be {dtype.__name__}")
        rid = self.rows.insert(row)
        self.columns.insert(row)
        for field, index in self.indexes.items():
            index.insert(row[field], rid)
        return {"page": rid.page, "slot": rid.slot}

    def create_index(self, field: str, kind: str = "auto", read_fraction: float = 0.8) -> str:
        if field not in SCHEMA:
            raise KeyError(f"unknown field {field}")
        if field in self.indexes:
            raise ValueError("field already indexed")
        if kind == "auto":
            kind = choose_index(read_fraction)
        if kind not in {"bplus", "lsm"}:
            raise ValueError("expected bplus/lsm/auto")
        index = (BPlusTree() if kind == "bplus" else
                 LSMTree(self.root / "indexes" / field, threshold=self.lsm_threshold))
        for rid, row in self.rows.scan():
            index.insert(row[field], rid)
        if isinstance(index, LSMTree):
            index.flush()
        self.indexes[field] = index
        self.metadata["indexes"][field] = kind
        self._write_metadata()
        return kind

    def select(self, field: str, value, use_index: bool = True) -> list[dict]:
        if field not in SCHEMA:
            raise KeyError(field)
        if use_index and field in self.indexes:
            return [self.rows.get(rid) for rid in self.indexes[field].lookup(value)]
        return [row for _, row in self.rows.scan() if row[field] == value]

    def select_range(self, field: str, low, high, use_index: bool = True) -> list[dict]:
        if field not in SCHEMA:
            raise KeyError(field)
        if use_index and field in self.indexes:
            return [self.rows.get(rid) for rid in self.indexes[field].range(low, high)]
        return [row for _, row in self.rows.scan() if low <= row[field] <= high]

    @staticmethod
    def _literal(field: str, text: str):
        dtype = SCHEMA[field]
        return dtype(text)

    def sql(self, statement: str) -> list[dict]:
        """Safe deliberately restricted SQL-like syntax (no dynamic eval/SQL engine).

        SELECT * FROM media WHERE category = 'music'
        SELECT * FROM media WHERE size_bytes BETWEEN 500 AND 2000
        """
        tokens = shlex.split(statement.strip().rstrip(";"))
        if len(tokens) not in (8, 10) or [t.upper() for t in tokens[:5]] != ["SELECT", "*", "FROM", "MEDIA", "WHERE"]:
            raise ValueError("supported form: SELECT * FROM media WHERE field = value | BETWEEN low AND high")
        field = tokens[5]
        if field not in SCHEMA:
            raise ValueError(f"unknown field: {field}")
        if len(tokens) == 8 and tokens[6] == "=":
            return self.select(field, self._literal(field, tokens[7]))
        if len(tokens) == 10 and tokens[6].upper() == "BETWEEN" and tokens[8].upper() == "AND":
            return self.select_range(field, self._literal(field, tokens[7]), self._literal(field, tokens[9]))
        raise ValueError("invalid SQL-like command")

    def stats(self) -> dict:
        return {
            "row_buffer": {**self.rows.buffer.stats, "hit_ratio": self.rows.buffer.hit_ratio},
            "column_buffers": self.columns.metrics(),
            "row_pages": self.rows.page_file.page_count,
            "index_types": dict(self.metadata["indexes"]),
        }

    def flush(self) -> None:
        self.rows.flush()
        self.columns.flush()
        for index in self.indexes.values():
            if isinstance(index, LSMTree):
                index.flush()

    def close(self) -> None:
        self.flush()
        self.rows.close()
        self.columns.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
