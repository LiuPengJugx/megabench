# MegaBench

English | [简体中文](README.zh-CN.md)

MegaBench is a trace-derived workload benchmark toolkit for external-table OLAP
mega-query detection.

It is intentionally closer to ClickBench-style realistic workload modeling than
to a full TPC-H/TPC-DS executable benchmark. The current release focuses on
publishing a privacy-preserving query workload: sanitized SQL shapes, structural
features, bucketed oracle metrics, labels, templates, a query generator, and a
synthetic wide-table data generator.

## Artifacts

This repository ships pre-built public artifacts under `data/public/`:

The artifacts are derived from a real production external-table OLAP workload
behind commercial BI, operational reporting, and ad hoc analysis for a
large-scale consumer content/recommendation business. The source workload mainly
reflects analyst and dashboard queries over wide event/fact tables, covering
content/item and user/device dimensions, recommendation strategy and experiment
analysis, traffic funnel and engagement metrics, commercial conversion
indicators, date/time slicing, JSON/array attribute extraction, metric
aggregation, ordering, and joins. Source clusters, environments,
database/table/column names, users, query IDs, and concrete business identifiers
are intentionally excluded.

- `workload.jsonl`: a public sample with sanitized SQL, pre-execution features,
  sanitized plan features, labels, and bucketed oracle metrics.
- `templates.json.gz`: template catalog mined from recurring query shapes.
- `stats.json`: private/public histogram summaries and validation metrics.
- `distribution_spec.json`: coarse synthetic-data distribution parameters for
  generating an executable wide-table dataset.
- `manifest.json`: artifact metadata, including scanned record counts, sample
  size, template count, and privacy boundary.
- `validation_report.md`: compact artifact quality and privacy report.

Raw SQL, raw query plans, real database/table/column names, users, query IDs,
exception strings, and exact runtime/IO metrics are not emitted.

## Environment

```bash
source ./env.sh
```

This creates `.venv` with `uv sync`, installs MegaBench in editable mode, and
activates the environment. No runtime dependency beyond the Python standard
library is required. The dev environment includes `pytest`.

## Generate Synthetic Dataset

MegaBench can generate a local ClickBench-like wide event/fact table using the
public `data/public/distribution_spec.json`. Official target scales are `1G`,
`10G`, `100G`, and `1000G`; the target refers to generated data-file size. The
default synthetic date window starts at `2024-01-01` for 30 days.

```bash
megabench dataset generate --scale 1G
```

Override the generated partition dates when needed:

```bash
megabench dataset generate --scale 1G --start-date 2024-06-01 --days 14
```

By default this writes CSV files under `data/generated/1G/`:

```text
data/generated/1G/
  manifest.json
  schema.json
  files.json
  distribution_spec.snapshot.json
  events_wide/
    event_date=2024-01-01/
      part-00000.csv
```

The synthetic table includes content/item, user/device, recommendation strategy,
experiment, geography/app, traffic source, engagement, commercial metrics,
JSON-like attributes, arrays, and additional wide dimension/metric columns. Data
generation is deterministic for a fixed seed.

Parquet output is supported when `pyarrow` is installed:

```bash
uv sync --extra parquet
megabench dataset generate --scale 1G --format parquet
```

Inspect a generated dataset:

```bash
megabench dataset inspect data/generated/1G
```

## Generate Synthetic Workload Rows

```bash
megabench generate
```

This samples from the shipped `data/public/` templates and writes
`artifacts/generated_workload.jsonl`.

Supported profiles:

- `balanced`: sample query patterns proportional to observed frequency.
- `mega_heavy`: upweight templates that often produce mega queries.
- `external_table_stress`: upweight large external-table scan templates.

## Record Format

```json
{
  "query_id": "mq_00000001",
  "template_id": "T0001",
  "sql": "SELECT count() FROM events_wide_001 WHERE c_0001 = {{int}}",
  "pre_execution_features": {
    "num_tables": 1,
    "num_columns": 3,
    "query_length_bucket": "100_1k",
    "query_type": "2",
    "event_hour": "13"
  },
  "plan_features": {
    "read": 1,
    "filter": 1,
    "aggregation": 1
  },
  "label": "normal",
  "oracle_buckets": {
    "read_bytes": "1GB_100GB",
    "lake_read_files": "100_1k",
    "query_duration_ms": "1s_10s"
  }
}
```

`oracle_buckets` are post-execution signals and should not be used as input
features for pre-execution mega-query detection.

## Privacy Boundary

The public artifact is produced through abstraction, not reversible masking:

- identifiers become role-like names such as `events_wide_001` and `c_0001`;
- string, numeric, and date literals become placeholders;
- unknown functions are mapped to `fn_001`, `fn_002`, ...;
- runtime metrics are bucketed;
- query patterns below the minimum count are dropped.

Before publishing, review `validation_report.md` and inspect a random sample of
`workload.jsonl` manually.

## Scope

MegaBench v0.2 is not a full database-engine benchmark. It is meant for:

- training and evaluating mega-query classifiers;
- studying realistic external-table OLAP workload distributions;
- generating synthetic wide-table data at `1G`, `10G`, `100G`, and `1000G`;
- generating larger synthetic query streams from observed templates.

The generated dataset is synthetic and executable, but it should not be treated
as a full database-engine benchmark yet. Engine-specific loaders and query
runners are future work.

## Maintainers

The `build` command regenerates `data/public/` from a private trace. It is not
needed by benchmark users.
