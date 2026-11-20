import tempfile
import unittest
from pathlib import Path
from mediadb.storage import BufferPool, ColumnStore, PageFile, RowStore, MAGIC, UINT32


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_restart_preserves_rows_and_rids(self):
        path = self.root / "rows.pages"
        rows = RowStore(path, capacity=2)
        ids = [rows.insert({"id": i, "text": "x" * 100}) for i in range(150)]
        self.assertGreater(rows.page_file.page_count, 1)
        rows.close()
        reopened = RowStore(path, capacity=2)
        self.assertEqual(reopened.get(ids[113]), {"id": 113, "text": "x" * 100})
        self.assertEqual(len(list(reopened.scan())), 150)
        reopened.close()

    def test_reject_oversized_record(self):
        store = RowStore(self.root / "oversized.pages", page_size=256)
        with self.assertRaises(ValueError):
            store.insert({"text": "x" * 400})
        store.close()

    def test_column_projection_restart(self):
        path = self.root / "columns"
        cols = ColumnStore(path, ["name", "score"])
        cols.insert({"name": "A", "score": 4})
        cols.insert({"name": "B", "score": 9})
        cols.close()
        cols = ColumnStore(path, ["name", "score"])
        self.assertEqual(list(cols.scan(["score"])), [{"score": 4}, {"score": 9}])
        self.assertEqual(list(cols.scan()), [{"name": "A", "score": 4}, {"name": "B", "score": 9}])
        cols.close()

    def test_lru_vs_fifo_eviction(self):
        for policy, expected_page in [("LRU", 1), ("FIFO", 0)]:
            page_file = PageFile(self.root / f"{policy}.pages")
            for _ in range(3):
                page_file.allocate()
            buffer = BufferPool(page_file, capacity=2, policy=policy)
            buffer.get(0)
            buffer.get(1)
            buffer.get(0)   # refresh page 0 only under LRU
            buffer.get(2)
            self.assertNotIn(expected_page, buffer.cache)
            self.assertEqual(buffer.stats["hits"], 1)
            self.assertEqual(buffer.stats["misses"], 3)
            self.assertEqual(buffer.stats["evictions"], 1)
            page_file.close()

    def test_dirty_page_flush(self):
        page_file = PageFile(self.root / "dirty.pages")
        page_file.allocate()
        buffer = BufferPool(page_file, capacity=1)
        buf = buffer.get(0)
        UINT32.pack_into(buf, 4, 0)
        buffer.mark_dirty(0)
        buffer.flush()
        self.assertEqual(buffer.stats["disk_writes"], 1)
        self.assertEqual(page_file.read(0)[:4], MAGIC)
        page_file.close()

    def test_cache_hits_and_scans(self):
        store = RowStore(self.root / "cache.pages")
        store.insert({"id": 5})
        store.buffer.reset_stats()
        list(store.scan())
        list(store.scan())
        self.assertGreater(store.buffer.hit_ratio, 0)
        store.close()


if __name__ == "__main__":
    unittest.main()
