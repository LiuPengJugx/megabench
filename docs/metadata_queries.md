# Metadata Query Sketches

MegaBench public artifacts are generated offline. These metadata query sketches
are for maintainers who need to refresh `distribution_spec.json`; they must be
reviewed before running in any production environment.

Avoid `COUNT(*)`, `DISTINCT`, sampling over large external tables, or any full
scan. Use catalog/system tables only.

For value-distribution profiling, prefer the guarded maintainer command:

```bash
megabench profile columns \
  --http-url http://host:8123/ \
  --user user \
  --password-env MEGABENCH_CH_PASSWORD \
  --trace data/private \
  --where "event_date = toDate('2024-01-01')" \
  --max-tables 5 \
  --rows-per-table 10000 \
  --max-execution-time 10 \
  --max-bytes-to-read 1073741824
```

The command sends a unique `query_id`, applies server-side limits, uses a
client-side timeout, and attempts `KILL QUERY` if execution fails or times out.
Its output is a redacted role-level profile: raw table names, column names, and
topK values are not emitted.

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
