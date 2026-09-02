"""Mine a private query trace into public MegaBench artifacts."""

from __future__ import annotations

import random
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .features import extract_public_record_features, extract_summary_record, summarize_rows
from .io import ensure_dir, extract_meta_from_line, find_trace_files, iter_jsonl_lines, write_json, write_json_gzip, write_jsonl
from .sanitize import sanitize_sql
from .util import counter_to_sorted_dict
from .validate import compare_summaries, render_markdown_report


@dataclass
class BuildResult:
    out_dir: Path
    source_file_count: int
    private_record_count: int
    public_record_count: int
    template_count: int


def build_public_artifacts(
    *,
    trace_path: str | Path,
    output_dir: str | Path,
    limit: int | None = None,
    min_pattern_count: int = 3,
    sample_size: int = 20000,
    sample_seed: int = 1,
    keep_rare: bool = False,
) -> BuildResult:
    source_files = find_trace_files(trace_path)
    if not source_files:
        raise FileNotFoundError(f"No raw_data.jsonl files found under {trace_path!s}")

    out_path = ensure_dir(output_dir)
    workload_path = ensure_dir(out_path / "workload")
    pattern_counts: Counter[str] = Counter()
    private_summary_rows: list[dict[str, Any]] = []

    for path, line_no, line in iter_jsonl_lines(source_files, limit=limit):
        pattern_key, meta, record = _pattern_key_from_line(line)
        pattern_counts[pattern_key] += 1
        if record is None:
            record = {"sql": "", "meta": meta}
        private_summary_rows.append(extract_summary_record(record, pattern_key))

    kept_keys = {
        key
        for key, count in pattern_counts.items()
        if keep_rare or count >= max(1, min_pattern_count)
    }
    template_ids = {
        key: f"T{idx:04d}"
        for idx, (key, _) in enumerate(
            sorted(
                ((key, count) for key, count in pattern_counts.items() if key in kept_keys),
                key=lambda item: (-item[1], item[0]),
            ),
            start=1,
        )
    }

    template_examples: dict[str, str] = {}
    template_aux: dict[str, dict[str, Any]] = {}
    template_stats: dict[str, dict[str, Any]] = {}
    sanitize_cache: dict[str, Any] = {}
    public_rows: list[dict[str, Any]] = []
    retained_summary_rows: list[dict[str, Any]] = []
    retained_count = 0
    rng = random.Random(sample_seed)
    sample_size = max(0, sample_size)

    for record_idx, (path, line_no, line) in enumerate(iter_jsonl_lines(source_files, limit=limit), start=1):
        pattern_key, _, record = _pattern_key_from_line(line)
        if pattern_key not in kept_keys:
            continue
        if record is None:
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_no}: {exc}") from exc
        retained_count += 1
        sql = record.get("sql") or ""
        sanitized = sanitize_cache.get(pattern_key)
        if sanitized is None:
            sanitized = sanitize_sql(sql)
            sanitize_cache[pattern_key] = sanitized
        features = extract_public_record_features(record, sanitized)
        template_examples.setdefault(pattern_key, sanitized.sql)
        template_aux.setdefault(
            pattern_key,
            {
                "placeholder_counts": sanitized.placeholder_counts,
                "table_count": sanitized.table_count,
                "column_count": sanitized.column_count,
            },
        )
        row = _public_shape(
            {
                "sanitized_sql": sanitized.sql,
                **features,
            },
            template_ids[pattern_key],
            record_idx,
        )
        _update_template_stats(template_stats, row)
        retained_summary_rows.append(_summary_shape_from_public(row))
        if sample_size == 0:
            continue
        if len(public_rows) < sample_size:
            public_rows.append(row)
        else:
            replacement_index = rng.randrange(retained_count)
            if replacement_index < sample_size:
                public_rows[replacement_index] = row

    for idx, row in enumerate(public_rows, start=1):
        row["query_id"] = f"mq_{idx:08d}"

    templates = _summarize_templates(template_stats, template_examples, template_aux, template_ids)
    private_summary = summarize_rows(private_summary_rows)
    retained_summary = summarize_rows(retained_summary_rows)
    public_summary = summarize_rows(public_rows)
    private_summary.get("histograms", {}).pop("template_id", None)
    retained_summary.get("histograms", {}).pop("template_id", None)
    public_summary.get("histograms", {}).pop("template_id", None)
    comparison = compare_summaries(private_summary, public_summary)

    write_jsonl(workload_path / "query_sample.jsonl", public_rows)
    write_json_gzip(workload_path / "query_templates.json.gz", {"templates": templates})
    write_json(
        workload_path / "workload_stats.json",
        {
            "private": private_summary,
            "retained": retained_summary,
            "public_sample": public_summary,
            "comparison": comparison,
        },
    )
    write_json(
        out_path / "benchmark_manifest.json",
        {
            "benchmark": "megabench",
            "format_version": 1,
            "artifact_files": {
                "workload_sample": "workload/query_sample.jsonl",
                "query_templates": "workload/query_templates.json.gz",
                "workload_stats": "workload/workload_stats.json",
                "validation_report": "workload/validation_report.md",
                "distribution_spec": "synthetic_dataset/distribution_spec.json",
            },
            "source_files": len(source_files),
            "private_records_scanned": len(private_summary_rows),
            "retained_records": retained_count,
            "public_workload_records": len(public_rows),
            "sample_size": sample_size,
            "sample_seed": sample_seed,
            "template_count": len(templates),
            "min_pattern_count": min_pattern_count,
            "keep_rare": keep_rare,
            "privacy_boundary": {
                "raw_sql_emitted": False,
                "raw_plan_emitted": False,
                "raw_identifiers_emitted": False,
                "runtime_metrics_are_bucketed": True,
            },
        },
    )
    (workload_path / "validation_report.md").write_text(
        render_markdown_report(
            comparison,
            min_pattern_count=min_pattern_count,
            source_file_count=len(source_files),
            retained_record_count=retained_count,
            sample_size=sample_size,
        ),
        encoding="utf-8",
    )

    return BuildResult(
        out_dir=out_path,
        source_file_count=len(source_files),
        private_record_count=len(private_summary_rows),
        public_record_count=len(public_rows),
        template_count=len(templates),
    )


def _pattern_key(record: dict[str, Any]) -> str:
    meta = record.get("meta") or {}
    normalized = meta.get("normalized_query_hash")
    if normalized not in (None, ""):
        return f"norm:{normalized}"
    return f"sql:{sanitize_sql(record.get('sql') or '').template_key}"


def _pattern_key_from_line(line: str) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    meta = extract_meta_from_line(line)
    normalized = meta.get("normalized_query_hash")
    if normalized not in (None, ""):
        return f"norm:{normalized}", meta, None
    record = json.loads(line)
    return _pattern_key(record), record.get("meta") or {}, record


def _public_shape(row: dict[str, Any], template_id: str, idx: int | None = None) -> dict[str, Any]:
    shaped = {
        "template_id": template_id,
        "sql": row["sanitized_sql"],
        "pre_execution_features": row["pre_execution_features"],
        "plan_features": row["plan_features"],
        "label": row["label"],
        "oracle_buckets": row["oracle_buckets"],
    }
    if idx is not None:
        shaped = {"query_id": f"mq_{idx:08d}", **shaped}
    return shaped


def _summarize_templates(
    template_stats: dict[str, dict[str, Any]],
    examples: dict[str, str],
    aux: dict[str, dict[str, Any]],
    template_ids: dict[str, str],
) -> list[dict[str, Any]]:
    key_by_template_id = {value: key for key, value in template_ids.items()}
    templates = []
    for template_id, stats in sorted(template_stats.items()):
        key = key_by_template_id[template_id]
        templates.append(
            {
                "template_id": template_id,
                "support": stats["support"],
                "sql": examples[key],
                "label_counts": counter_to_sorted_dict(stats["label_counts"]),
                "query_type_counts": counter_to_sorted_dict(stats["query_type_counts"]),
                "pre_feature_hists": {name: counter_to_sorted_dict(counter) for name, counter in stats["pre_feature_hists"].items()},
                "oracle_bucket_hists": {name: counter_to_sorted_dict(counter) for name, counter in stats["oracle_bucket_hists"].items()},
                "shape": aux.get(key, {}),
            }
        )
    return templates


def _update_template_stats(template_stats: dict[str, dict[str, Any]], row: dict[str, Any]) -> None:
    template_id = row["template_id"]
    stats = template_stats.setdefault(
        template_id,
        {
            "support": 0,
            "label_counts": Counter(),
            "query_type_counts": Counter(),
            "pre_feature_hists": defaultdict(Counter),
            "oracle_bucket_hists": defaultdict(Counter),
        },
    )
    stats["support"] += 1
    stats["label_counts"][row["label"]] += 1
    pre = row.get("pre_execution_features") or {}
    stats["query_type_counts"][str(pre.get("query_type", "unknown"))] += 1
    for name in ("query_length_bucket", "view_count_bucket", "unknown_function_count_bucket", "event_hour"):
        stats["pre_feature_hists"][name][str(pre.get(name, "unknown"))] += 1
    for name, value in (row.get("oracle_buckets") or {}).items():
        stats["oracle_bucket_hists"][name][str(value)] += 1


def _summary_shape_from_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "template_id": row["template_id"],
        "pre_execution_features": row["pre_execution_features"],
        "oracle_buckets": row["oracle_buckets"],
        "label": row["label"],
    }
