# Metadata Query Sketches

The first MegaBench release does not execute database metadata queries. These
sketches are only for a future executable edition and must be reviewed before
running in any production environment.

Avoid `COUNT(*)`, `DISTINCT`, sampling over large external tables, or any full
scan. Use catalog/system tables only.

```sql
-- Column names and types for selected tables.
SELECT
  database,
  table,
  name,
  type
FROM system.columns
WHERE database IN ({database_list})
  AND table IN ({table_list});
```

```sql
-- Table engines and coarse metadata known by the catalog.
SELECT
  database,
  name,
  engine,
  total_rows,
  total_bytes
FROM system.tables
WHERE database IN ({database_list})
  AND name IN ({table_list});
```

```sql
-- Optional partition metadata, if the catalog exposes it without scanning data.
SELECT
  database,
  table,
  partition,
  rows,
  bytes_on_disk
FROM system.parts
WHERE active
  AND database IN ({database_list})
  AND table IN ({table_list});
```
