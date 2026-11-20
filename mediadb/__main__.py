"""CLI for demonstrating the real custom DBMS, not a separate external DB."""
import argparse
import json
from .database import MediaDatabase


def main():
    parser = argparse.ArgumentParser(description="MediaDB storage and indexing prototype")
    parser.add_argument("--db", default="demo_database", help="persistent data directory")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("demo")
    index = sub.add_parser("index")
    index.add_argument("field")
    index.add_argument("--kind", choices=["bplus", "lsm", "auto"], default="auto")
    index.add_argument("--read-fraction", type=float, default=0.8)
    query = sub.add_parser("query")
    query.add_argument("statement", help="single SQL-like statement in quotes")
    sub.add_parser("stats")
    args = parser.parse_args()
    with MediaDatabase(args.db) as db:
        if args.action == "demo":
            if db.rows.page_file.page_count == 0:
                for i, media_type in enumerate(["video", "audio", "image", "video", "image"]):
                    db.insert({"media_id": f"M{i:03}", "title": f"Sample asset {i}",
                               "media_type": media_type, "category": "education", "creator": "Demo",
                               "size_bytes": 1000 + 250 * i, "duration_seconds": float(i * 10),
                               "created_at": "2026-11-20"})
            if "media_id" not in db.indexes:
                db.create_index("media_id", "bplus")
            if "media_type" not in db.indexes:
                db.create_index("media_type", "lsm")
            print(json.dumps({"lookup": db.sql("SELECT * FROM media WHERE media_id = M002"),
                              "range": db.sql("SELECT * FROM media WHERE media_id BETWEEN M001 AND M003"),
                              "stats": db.stats()}, indent=2))
        elif args.action == "index":
            print("Index created:", args.field, db.create_index(args.field, args.kind, args.read_fraction))
        elif args.action == "query":
            print(json.dumps(db.sql(args.statement), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(db.stats(), indent=2))


if __name__ == "__main__":
    main()
