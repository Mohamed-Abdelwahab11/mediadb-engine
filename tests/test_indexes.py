import random
import tempfile
import unittest
from pathlib import Path
from mediadb.indexes import BPlusTree, LSMTree, choose_index
from mediadb.storage import RecordID


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_bplus_multi_level_random_keys(self):
        tree = BPlusTree(order=3)
        values = list(range(150))
        random.Random(42).shuffle(values)
        for value in values:
            tree.insert(value, RecordID(value, 0))
        self.assertGreater(tree.node_count(), 10)
        for value in range(150):
            self.assertEqual(tree.lookup(value), [RecordID(value, 0)])
        self.assertEqual(tree.lookup(777), [])
        self.assertEqual(tree.range(40, 45), [RecordID(i, 0) for i in range(40, 46)])
        self.assertEqual(tree.range(55, 54), [])

    def test_bplus_duplicate_values(self):
        tree = BPlusTree(order=3)
        tree.insert("video", RecordID(0, 0))
        tree.insert("video", RecordID(0, 1))
        self.assertEqual(tree.lookup("video"), [RecordID(0, 0), RecordID(0, 1)])

    def test_lsm_flush_compaction_and_restart(self):
        path = Path(self.temp.name) / "lsm"
        tree = LSMTree(path, threshold=3, max_runs=2)
        for i in range(30):
            tree.insert(i % 5, RecordID(i, 0))
        tree.flush()
        self.assertLessEqual(len(tree.runs), 2)
        reopened = LSMTree(path, threshold=3, max_runs=2)
        self.assertEqual({r.page for r in reopened.lookup(2)}, set(range(2, 30, 5)))
        self.assertEqual(len(reopened.range(1, 3)), 18)
        self.assertGreater(reopened.disk_bytes, 0)

    def test_automatic_index_selection(self):
        self.assertEqual(choose_index(0.9), "bplus")
        self.assertEqual(choose_index(0.1), "lsm")
        self.assertEqual(choose_index(0.6), "bplus")
        with self.assertRaises(ValueError):
            choose_index(1.5)


if __name__ == "__main__":
    unittest.main()
