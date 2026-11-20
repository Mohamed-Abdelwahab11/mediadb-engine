# Examples

From the repository root:

```bash
python3 -m mediadb --db demo_database demo
python3 -m mediadb --db demo_database stats
python3 -m mediadb --db demo_database query "SELECT * FROM media WHERE media_id = M002"
```

The demo writes a local database inside `demo_database/`, which is excluded from version control. Delete this directory to start with fresh sample records.
