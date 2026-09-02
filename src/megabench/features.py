"""Feature extraction and coarse bucketing."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .sanitize import SanitizedSQL


BYTE_BUCKETS = [
    (0, "0"),
    (1 << 20, "lt_1MB"),
    (1 << 30, "1MB_1GB"),
    (100 * (1 << 30), "1GB_100GB"),
    (1 << 40, "100GB_1TB"),
    (10 * (1 << 40), "1TB_10TB"),
]

COUNT_BUCKETS = [
    (0, "0"),
    (10, "1_10"),
    (100, "10_100"),
    (1000, "100_1k"),
    (10000, "1k_10k"),
    (100000, "10k_100k"),
    (1000000, "100k_1m"),
    (10000000, "1m_10m"),
    (100000000, "10m_100m"),
    (1000000000, "100m_1b"),
]

LATENCY_BUCKETS_MS = [
    (0, "0"),
    (100, "lt_100ms"),
    (1000, "100ms_1s"),
    (10000, "1s_10s"),
    (60000, "10s_60s"),
    (300000, "1m_5m"),
    (1800000, "5m_30m"),
]

PLAN_OPERATORS = {
    "aggregation": re.compile(r"\b(aggregat|group by)\b", re.I),
    "filter": re.compile(r"\b(filter|where|prewhere)\b", re.I),
    "join": re.compile(r"\b(join|hashjoin|mergejoin)\b", re.I),
    "limit": re.compile(r"\blimit\b", re.I),
    "projection": re.compile(r"\b(project|expression)\b", re.I),
    "read": re.compile(r"\b(read|scan|source)\b", re.I),
    "sort": re.compile(r"\b(sort|order by)\b", re.I),
    "union": re.compile(r"\bunion\b", re.I),
    "window": re.compile(r"\bwindow\b", re.I),
}


def extract_public_record_features(record: dict[str, Any], sanitized: SanitizedSQL) -> dict[str, Any]:
    meta = record.get("meta") or {}
    sql = record.get("sql") or ""

    return {
        "pre_execution_features": {
            "num_tables": _safe_int(meta.get("num_tables"), sanitized.table_count),
            "num_columns": _safe_int(meta.get("num_columns"), sanitized.column_count),
            "query_length_bucket": bucket_count(_safe_int(meta.get("query_length"), len(sql))),
            "query_type": str(meta.get("query_type", "unknown")),
            "view_count_bucket": bucket_count(_safe_int(meta.get("view_cnt"), 0)),
            "placeholder_counts": sanitized.placeholder_counts,
            "sql_operator_counts": sanitized.operator_counts,
            "unknown_function_count_bucket": bucket_count(sanitized.unknown_function_count),
            "event_hour": _event_hour(meta.get("event_time")),
        },
        "plan_features": extract_plan_features(record.get("query_plan") or ""),
        "oracle_buckets": {
            "read_rows": bucket_count(_safe_int(meta.get("read_rows"), 0)),
            "read_bytes": bucket_bytes(_safe_int(meta.get("read_bytes"), 0)),
            "lake_read_size": bucket_bytes(_safe_int(meta.get("lake_read_size"), 0)),
            "lake_read_files": bucket_count(_safe_int(meta.get("lake_read_files"), 0)),
            "lake_read_partitions": bucket_count(_safe_int(meta.get("lake_read_partitions"), 0)),
            "memory_usage": bucket_bytes(_safe_int(meta.get("memory_usage"), 0)),
            "cpu_time_microseconds": bucket_count(_safe_int(meta.get("cpu_time_microseconds"), 0)),
            "page_cache_hits": bucket_count(_safe_int(meta.get("page_cache_hits"), 0)),
            "query_duration_ms": bucket_latency_ms(_safe_int(meta.get("query_duration_ms"), 0)),
            "exception_code_group": exception_code_group(meta.get("exception_code")),
        },
        "label": normalize_label(meta.get("raw_label")),
    }


def extract_summary_record(record: dict[str, Any], template_id: str = "private") -> dict[str, Any]:
    meta = record.get("meta") or {}
    sql = record.get("sql") or ""
    return {
        "template_id": template_id,
        "pre_execution_features": {
            "query_length_bucket": bucket_count(_safe_int(meta.get("query_length"), len(sql))),
            "query_type": str(meta.get("query_type", "unknown")),
            "event_hour": _event_hour(meta.get("event_time")),
        },
        "oracle_buckets": {
            "read_bytes": bucket_bytes(_safe_int(meta.get("read_bytes"), 0)),
            "lake_read_size": bucket_bytes(_safe_int(meta.get("lake_read_size"), 0)),
            "lake_read_files": bucket_count(_safe_int(meta.get("lake_read_files"), 0)),
            "query_duration_ms": bucket_latency_ms(_safe_int(meta.get("query_duration_ms"), 0)),
        },
        "label": normalize_label(meta.get("raw_label")),
    }


def extract_plan_features(plan: str) -> dict[str, Any]:
    plan = plan or ""
    counts = {name: len(pattern.findall(plan)) for name, pattern in PLAN_OPERATORS.items()}
    max_indent = 0
    for line in plan.splitlines():
        stripped = line.lstrip()
        max_indent = max(max_indent, len(line) - len(stripped))
    counts["plan_line_count_bucket"] = bucket_count(len(plan.splitlines()))
    counts["plan_length_bucket"] = bucket_count(len(plan))
    counts["max_indent_bucket"] = bucket_count(max_indent)
    return counts


def normalize_label(value: Any) -> str:
    raw = str(value or "unknown").strip().lower()
    if not raw:
        return "unknown"
    if "mega" in raw or raw in {"1", "true", "positive", "pos", "large"}:
        return "mega"
    if "normal" in raw or raw in {"0", "false", "negative", "neg", "small"}:
        return "normal"
    return re.sub(r"[^a-z0-9_]+", "_", raw).strip("_") or "unknown"


def bucket_bytes(value: int) -> str:
    if value <= 0:
        return "0"
    for upper, name in BYTE_BUCKETS:
        if value <= upper:
            return name
    return "10TB_plus"


def bucket_count(value: int) -> str:
    if value <= 0:
        return "0"
    for upper, name in COUNT_BUCKETS:
        if value <= upper:
            return name
    return "1b_plus"


def bucket_latency_ms(value: int) -> str:
    if value <= 0:
        return "0"
    for upper, name in LATENCY_BUCKETS_MS:
        if value <= upper:
            return name
    return "30m_plus"


def exception_code_group(value: Any) -> str:
    code = _safe_int(value, 0)
    if code == 0:
        return "none"
    if code < 100:
        return "lt_100"
    if code < 1000:
        return "100_999"
    return "1000_plus"


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    histograms: dict[str, Counter[str]] = {
        "label": Counter(),
        "template_id": Counter(),
        "query_type": Counter(),
        "query_length_bucket": Counter(),
        "read_bytes": Counter(),
        "lake_read_size": Counter(),
        "lake_read_files": Counter(),
        "query_duration_ms": Counter(),
        "event_hour": Counter(),
    }

    for row in rows:
        pre = row.get("pre_execution_features") or {}
        oracle = row.get("oracle_buckets") or {}
        histograms["label"][row.get("label", "unknown")] += 1
        histograms["template_id"][row.get("template_id", "unknown")] += 1
        histograms["query_type"][str(pre.get("query_type", "unknown"))] += 1
        histograms["query_length_bucket"][str(pre.get("query_length_bucket", "unknown"))] += 1
        histograms["read_bytes"][str(oracle.get("read_bytes", "unknown"))] += 1
        histograms["lake_read_size"][str(oracle.get("lake_read_size", "unknown"))] += 1
        histograms["lake_read_files"][str(oracle.get("lake_read_files", "unknown"))] += 1
        histograms["query_duration_ms"][str(oracle.get("query_duration_ms", "unknown"))] += 1
        histograms["event_hour"][str(pre.get("event_hour", "unknown"))] += 1

    return {
        "record_count": len(rows),
        "histograms": {name: dict(counter) for name, counter in histograms.items()},
    }


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _event_hour(value: Any) -> str:
    if not value:
        return "unknown"
    text = str(value)
    match = re.search(r"\b(\d{2}):\d{2}:\d{2}", text)
    return match.group(1) if match else "unknown"
