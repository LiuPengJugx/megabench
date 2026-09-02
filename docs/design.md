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
```

## Why Workload-Only First

The target task is pre-execution mega-query detection. For this task, realistic
query shapes, labels, and workload distributions are more important than an
executable database. A synthetic data generator can be added later for engine
benchmarking, but it is not required for classifier evaluation.

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
