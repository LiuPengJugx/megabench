# Design

MegaBench separates the private extraction pipeline from public artifacts.

## Pipeline

```text
private raw_data.jsonl
  -> SQL abstraction
  -> plan feature extraction
  -> template mining
  -> rare-template filtering
  -> public workload + template catalog + distribution report
  -> synthetic workload generation

public distribution_spec.json
  -> deterministic wide-table row generation
  -> partitioned CSV or optional Parquet files
  -> dataset manifest + schema + file index
```

## Dataset Generation

The core benchmark remains pre-execution mega-query detection, but v0.2 also
ships a synthetic wide-table generator. The generator uses only public coarse
distribution parameters:

- temporal and hourly workload skew;
- head/tail item, user, and author ID distributions;
- categorical mixes for scene, strategy, experiment, region, app, device, and
  traffic source;
- sparse commercial metrics and engagement counters;
- JSON-like attributes and array-like columns;
- additional synthetic wide-table dimension, flag, and metric columns.

Official target sizes are `1G`, `10G`, `100G`, and `1000G`. Generated data is
partitioned by `event_date`. CSV requires only the Python standard library;
Parquet is optional and requires `pyarrow`.

## Public vs Oracle Fields

Public model inputs should use:

- `sql`
- `pre_execution_features`
- `plan_features`, if available before execution in the target deployment

Oracle-only fields include:

- `read_rows`
- `read_bytes`
- `lake_read_size`
- `lake_read_files`
- `memory_usage`
- `query_duration_ms`
- `cpu_time_microseconds`

These fields are emitted only as coarse buckets for analysis and validation.
They should not be used as model inputs for pre-execution detection.

## Template Privacy Rules

- Do not emit raw SQL.
- Do not emit raw query plans.
- Do not emit real database, table, column, user, query ID, or exception text.
- Drop query patterns below `min_pattern_count` by default.
- Review high-risk SQL functions and rare syntax manually before release.

## Validation Signals

The validation report compares private and public distributions using coarse
histograms and Jensen-Shannon divergence. Important histograms include:

- label distribution;
- query type;
- query length bucket;
- event hour;
- scanned bytes bucket;
- lake read size bucket;
- lake read files bucket;
- duration bucket.

For model papers, also report chronological and template-heldout splits.
