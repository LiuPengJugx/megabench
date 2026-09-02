# MegaBench

English | [简体中文](README.zh-CN.md)

MegaBench is a trace-derived workload benchmark toolkit for external-table OLAP
mega-query detection.

It is intentionally closer to ClickBench-style realistic workload modeling than
to a full TPC-H/TPC-DS executable benchmark. The first release focuses on
publishing a privacy-preserving query workload: sanitized SQL shapes, structural
features, bucketed oracle metrics, labels, templates, and a generator.

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

MegaBench v0.1 is not an executable database benchmark. It is meant for:

- training and evaluating mega-query classifiers;
- studying realistic external-table OLAP workload distributions;
- generating larger synthetic query streams from observed templates.

A future executable edition can add a synthetic wide-table dataset, loader, and
engine-specific runners.

## Maintainers

The `build` command regenerates `data/public/` from a private trace. It is not
needed by benchmark users.
