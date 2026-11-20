import tempfile
import unittest
from mediadb.database import MediaDatabase
from mediadb.objects import ObjectStore


def media(i):
    return {"media_id": f"M{i:04}", "title": f"Sample video {i}",
            "media_type": "video" if i % 2 == 0 else "audio",
            "category": "education" if i % 3 else "news", "creator": "Tester",
            "size_bytes": i * 100, "duration_seconds": float(i) * 0.5,
            "created_at": "2026-11-20"}


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_indexes_query_and_restart(self):
        with MediaDatabase(self.temp.name, lsm_threshold=5) as db:
            for i in range(61):
                db.insert(media(i))
            self.assertEqual(db.create_index("media_id", "bplus"), "bplus")
            self.assertEqual(db.create_index("media_type", "lsm"), "lsm")
            self.assertEqual(db.create_index("size_bytes", "auto", read_fraction=0.8), "bplus")
            self.assertEqual(db.create_index("category", "auto", read_fraction=0.2), "lsm")
            self.assertEqual(db.select("media_id", "M0042"), [media(42)])
            self.assertEqual(db.select("media_id", "M0042", use_index=False), [media(42)])
            self.assertEqual(db.sql("SELECT * FROM media WHERE media_id = M0042"), [media(42)])
            self.assertEqual(len(db.sql("SELECT * FROM media WHERE size_bytes BETWEEN 200 AND 500")), 4)
            self.assertEqual(len(db.select("media_type", "video")), 31)
            db.insert(media(100))
            self.assertEqual(db.select("media_id", "M0100"), [media(100)])
            self.assertEqual(len(db.select("media_type", "video")), 32)
        with MediaDatabase(self.temp.name, lsm_threshold=5) as reopened:
            self.assertEqual(reopened.select("media_id", "M0100"), [media(100)])
            self.assertEqual(len(reopened.select("media_type", "video")), 32)
            self.assertEqual(len(reopened.select_range("size_bytes", 100, 300)), 3)
            self.assertEqual(len(list(reopened.columns.scan(["category"]))), 62)

    def test_sql_rejects_unsupported_commands(self):
        with MediaDatabase(self.temp.name) as db:
            for sql in ("DROP TABLE media", "SELECT * FROM media; DROP TABLE media", "SELECT username FROM media WHERE media_id = M001", "SELECT * FROM media WHERE wrong_field = foo"):
                with self.assertRaises(ValueError):
                    db.sql(sql)

    def test_object_store_and_codec_hook(self):
        class XorCodec:
            def encode(self, plain):
                return bytes(b ^ 0x5a for b in plain)
            def decode(self, stored):
                return self.encode(stored)

        objects = ObjectStore(self.temp.name, codec=XorCodec())
        payload = b"synthetic video thumbnail data"
        key = objects.put(payload)
        self.assertEqual(objects.get(key), payload)
        self.assertEqual(objects.put(payload), key)
        self.assertNotEqual((objects.root / key[:2] / key).read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
