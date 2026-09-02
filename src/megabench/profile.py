"""Private column profiling with guarded ClickHouse HTTP queries."""

from __future__ import annotations

import base64
import json
import os
import socket
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .io import ensure_dir, find_trace_files, iter_jsonl, write_json


DEFAULT_PROFILE_OUTPUT = Path("artifacts/private/column_profile.json")
ID_HINTS = ("id", "uid", "user", "device", "item", "author", "session", "order", "shop", "campaign")
CATEGORICAL_HINTS = (
    "type",
    "scene",
    "strategy",
    "experiment",
    "bucket",
    "region",
    "city",
    "version",
    "source",
    "status",
    "category",
)
METRIC_HINTS = (
    "cnt",
    "count",
    "num",
    "duration",
    "cost",
    "revenue",
    "price",
    "amount",
    "score",
    "rate",
    "ratio",
)


@dataclass(frozen=True)
class ProfileResult:
    output_path: Path
    table_count: int
    column_count: int
    query_count: int
    killed_queries: int


@dataclass(frozen=True)
class ColumnInfo:
    database: str
    table: str
    name: str
    type: str
    role: str


class ClickHouseHTTPClient:
    def __init__(
        self,
        *,
        url: str,
        user: str | None,
        password: str | None,
        timeout: int,
        query_prefix: str,
    ) -> None:
        self.url = url
        self.user = user
        self.password = password
        self.timeout = timeout
        self.query_prefix = query_prefix
        self.query_count = 0
        self.killed_queries = 0

    def execute_json(self, sql: str, *, settings: dict[str, Any]) -> dict[str, Any]:
        query_id = f"{self.query_prefix}_{uuid.uuid4().hex[:12]}"
        params = {"query_id": query_id, **{key: str(value) for key, value in settings.items()}}
        full_url = self.url + ("&" if "?" in self.url else "?") + urlencode(params)
        body = sql.rstrip()
        if "FORMAT JSON" not in body.upper():
            body = f"{body}\nFORMAT JSON"
        request = Request(full_url, data=body.encode("utf-8"), method="POST")
        if self.user is not None:
            token = base64.b64encode(f"{self.user}:{self.password or ''}".encode("utf-8")).decode("ascii")
            request.add_header("Authorization", f"Basic {token}")
        self.query_count += 1
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
            return json.loads(payload)
        except Exception:
            self.kill_query(query_id)
            raise

    def kill_query(self, query_id: str) -> None:
        sql = "KILL QUERY WHERE query_id = '{}' SYNC FORMAT JSON".format(query_id.replace("'", "\\'"))
        request = Request(self.url, data=sql.encode("utf-8"), method="POST")
        if self.user is not None:
            token = base64.b64encode(f"{self.user}:{self.password or ''}".encode("utf-8")).decode("ascii")
            request.add_header("Authorization", f"Basic {token}")
        try:
            with urlopen(request, timeout=min(self.timeout, 10)) as response:
                response.read()
            self.killed_queries += 1
        except Exception:
            self.killed_queries += 1


def profile_columns(
    *,
    http_url: str,
    output_path: str | Path = DEFAULT_PROFILE_OUTPUT,
    user: str | None = None,
    password: str | None = None,
    password_env: str | None = None,
    trace_path: str | Path | None = None,
    trace_limit: int | None = 50_000,
    tables: list[str] | None = None,
    where: str | None = None,
    rows_per_table: int = 10_000,
    max_tables: int = 5,
    max_columns_per_role: int = 4,
    max_execution_time: int = 10,
    max_bytes_to_read: int = 1 << 30,
    timeout: int = 15,
    query_prefix: str = "megabench_profile",
) -> ProfileResult:
    password = password if password is not None else (os.environ.get(password_env) if password_env else None)
    table_refs = _resolve_tables(trace_path=trace_path, trace_limit=trace_limit, tables=tables, max_tables=max_tables)
    if not table_refs:
        raise ValueError("No tables to profile. Pass --table or --trace.")

    client = ClickHouseHTTPClient(
        url=http_url,
        user=user,
        password=password,
        timeout=timeout,
        query_prefix=query_prefix,
    )
    settings = {
        "max_execution_time": max_execution_time,
        "max_bytes_to_read": max_bytes_to_read,
        "max_result_rows": 1000,
        "result_overflow_mode": "break",
        "skip_unavailable_shards": 1,
    }
    started = time.time()
    schema_rows = _fetch_schema(client, table_refs, settings=settings)
    columns_by_table = _select_profile_columns(schema_rows, max_columns_per_role=max_columns_per_role)

    table_profiles = []
    errors = []
    for table_index, ((database, table), columns) in enumerate(columns_by_table.items(), start=1):
        if not columns:
            continue
        try:
            profile = _profile_table(
                client,
                database=database,
                table=table,
                columns=columns,
                where=where,
                rows_per_table=rows_per_table,
                settings=settings,
                table_index=table_index,
            )
            table_profiles.append(profile)
        except Exception as exc:
            errors.append(
                {
                    "table_index": table_index,
                    "error_type": type(exc).__name__,
                    "message": "profile_query_failed_or_timed_out",
                }
            )

    summary = _summarize_profiles(table_profiles)
    output = {
        "profile_version": 1,
        "generated_at_unix": int(time.time()),
        "source": {
            "raw_identifiers_emitted": False,
            "raw_topk_values_emitted": False,
            "table_count_requested": len(table_refs),
            "table_count_profiled": len(table_profiles),
        },
        "safety": {
            "max_execution_time": max_execution_time,
            "max_bytes_to_read": max_bytes_to_read,
            "rows_per_table": rows_per_table,
            "timeout": timeout,
            "query_prefix": query_prefix,
            "queries_executed": client.query_count,
            "kill_attempts": client.killed_queries,
        },
        "summary": summary,
        "tables": table_profiles,
        "errors": errors,
        "recommended_distribution_patch": _recommended_patch(summary),
        "elapsed_sec": round(time.time() - started, 3),
    }
    out_path = Path(output_path)
    ensure_dir(out_path.parent)
    write_json(out_path, output, pretty=True)
    return ProfileResult(
        output_path=out_path,
        table_count=len(table_profiles),
        column_count=sum(len(table.get("columns", [])) for table in table_profiles),
        query_count=client.query_count,
        killed_queries=client.killed_queries,
    )


def _resolve_tables(
    *,
    trace_path: str | Path | None,
    trace_limit: int | None,
    tables: list[str] | None,
    max_tables: int,
) -> list[tuple[str, str]]:
    counter: Counter[tuple[str, str]] = Counter()
    for table in tables or []:
        parsed = _parse_table_ref(table)
        counter[parsed] += 1_000_000_000
    if trace_path is not None:
        for _, record in iter_jsonl(find_trace_files(trace_path), limit=trace_limit):
            meta = record.get("meta") or {}
            for table_ref in _read_meta_list(meta.get("tables")):
                try:
                    counter[_parse_table_ref(table_ref)] += 1
                except ValueError:
                    continue
    return [table for table, _ in counter.most_common(max_tables)]


def _read_meta_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return [str(value)]


def _parse_table_ref(table_ref: str) -> tuple[str, str]:
    if "." not in table_ref:
        raise ValueError(f"Table must be database.table: {table_ref!r}")
    database, table = table_ref.split(".", 1)
    if not database or not table:
        raise ValueError(f"Table must be database.table: {table_ref!r}")
    return database, table


def _fetch_schema(
    client: ClickHouseHTTPClient,
    table_refs: list[tuple[str, str]],
    *,
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    where = " OR ".join(
        f"(database = {_sql_string(database)} AND table = {_sql_string(table)})"
        for database, table in table_refs
    )
    sql = f"""
SELECT DISTINCT
    database,
    table,
    name,
    type
FROM system.columns
WHERE {where}
ORDER BY database, table, name
"""
    payload = client.execute_json(sql, settings=settings)
    return list(payload.get("data") or [])


def _select_profile_columns(
    rows: list[dict[str, Any]],
    *,
    max_columns_per_role: int,
) -> dict[tuple[str, str], list[ColumnInfo]]:
    grouped: dict[tuple[str, str], dict[str, list[ColumnInfo]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        database = str(row.get("database", ""))
        table = str(row.get("table", ""))
        name = str(row.get("name", ""))
        dtype = str(row.get("type", ""))
        role = _infer_role(name, dtype)
        if role == "skip":
            continue
        grouped[(database, table)][role].append(ColumnInfo(database, table, name, dtype, role))

    selected: dict[tuple[str, str], list[ColumnInfo]] = {}
    for table, by_role in grouped.items():
        columns: list[ColumnInfo] = []
        for role in ("id", "categorical", "metric", "string"):
            columns.extend(by_role.get(role, [])[:max_columns_per_role])
        selected[table] = columns
    return selected


def _infer_role(name: str, dtype: str) -> str:
    lower_name = name.lower()
    lower_type = dtype.lower()
    is_numeric = any(marker in lower_type for marker in ("int", "float", "decimal"))
    is_string = "string" in lower_type or "enum" in lower_type
    if any(hint in lower_name for hint in METRIC_HINTS) and is_numeric:
        return "metric"
    if any(hint in lower_name for hint in ID_HINTS):
        return "id"
    if any(hint in lower_name for hint in CATEGORICAL_HINTS) and (is_string or is_numeric):
        return "categorical"
    if is_string:
        return "string"
    if is_numeric:
        return "metric"
    return "skip"


def _profile_table(
    client: ClickHouseHTTPClient,
    *,
    database: str,
    table: str,
    columns: list[ColumnInfo],
    where: str | None,
    rows_per_table: int,
    settings: dict[str, Any],
    table_index: int,
) -> dict[str, Any]:
    expressions = ["count() AS sampled_rows"]
    for idx, column in enumerate(columns, start=1):
        column_ref = _ident(column.name)
        prefix = f"c{idx:02d}"
        expressions.append(f"uniqCombined64(toString({column_ref})) AS {prefix}_uniq")
        expressions.append(f"countIf(isNull({column_ref})) AS {prefix}_nulls")
        if column.role in {"metric"}:
            expressions.append(f"quantilesTDigest(0.5, 0.9, 0.99)(toFloat64OrZero(toString({column_ref}))) AS {prefix}_q")
        else:
            expressions.append(f"length(topK(20)(toString({column_ref}))) AS {prefix}_topk_size")
    where_clause = f"WHERE {where}" if where else ""
    select_expressions = ",\n    ".join(expressions)
    source_columns = ", ".join(_ident(column.name) for column in columns)
    sql = f"""
SELECT
    {select_expressions}
FROM
(
    SELECT {source_columns}
    FROM {_ident(database)}.{_ident(table)}
    {where_clause}
    LIMIT {int(rows_per_table)}
)
"""
    payload = client.execute_json(sql, settings=settings)
    data = payload.get("data") or []
    row = data[0] if data else {}
    sampled_rows = _to_int(row.get("sampled_rows"))
    profile_columns = []
    for idx, column in enumerate(columns, start=1):
        prefix = f"c{idx:02d}"
        nulls = _to_int(row.get(f"{prefix}_nulls"))
        uniq = _to_int(row.get(f"{prefix}_uniq"))
        entry = {
            "column_index": idx,
            "role": column.role,
            "type_family": _type_family(column.type),
            "sampled_rows": sampled_rows,
            "null_rate_bucket": _rate_bucket(nulls / sampled_rows if sampled_rows else 0.0),
            "approx_distinct_bucket": _count_bucket(uniq),
        }
        if column.role == "metric":
            entry["quantile_buckets"] = [_numeric_bucket(value) for value in row.get(f"{prefix}_q", [])]
        else:
            entry["topk_size_bucket"] = _count_bucket(_to_int(row.get(f"{prefix}_topk_size")))
        profile_columns.append(entry)

    return {
        "table_index": table_index,
        "sampled_rows": sampled_rows,
        "column_count_profiled": len(profile_columns),
        "columns": profile_columns,
    }


def _summarize_profiles(table_profiles: list[dict[str, Any]]) -> dict[str, Any]:
    role_counts: Counter[str] = Counter()
    distinct_buckets: dict[str, Counter[str]] = defaultdict(Counter)
    null_buckets: dict[str, Counter[str]] = defaultdict(Counter)
    type_families: dict[str, Counter[str]] = defaultdict(Counter)
    metric_quantile_buckets: dict[str, Counter[str]] = defaultdict(Counter)
    for table in table_profiles:
        for column in table.get("columns", []):
            role = str(column.get("role", "unknown"))
            role_counts[role] += 1
            distinct_buckets[role][str(column.get("approx_distinct_bucket", "unknown"))] += 1
            null_buckets[role][str(column.get("null_rate_bucket", "unknown"))] += 1
            type_families[role][str(column.get("type_family", "unknown"))] += 1
            quantiles = column.get("quantile_buckets") or []
            for idx, bucket in enumerate(quantiles):
                metric_quantile_buckets[f"q{idx}"][str(bucket)] += 1
    return {
        "table_count": len(table_profiles),
        "sampled_rows_total": sum(_to_int(table.get("sampled_rows")) for table in table_profiles),
        "role_counts": {key: role_counts[key] for key in sorted(role_counts)},
        "approx_distinct_buckets": {
            role: {key: counter[key] for key in sorted(counter)}
            for role, counter in sorted(distinct_buckets.items())
        },
        "null_rate_buckets": {
            role: {key: counter[key] for key in sorted(counter)}
            for role, counter in sorted(null_buckets.items())
        },
        "type_families": {
            role: {key: counter[key] for key in sorted(counter)}
            for role, counter in sorted(type_families.items())
        },
        "metric_quantile_buckets": {
            name: {key: counter[key] for key in sorted(counter)}
            for name, counter in sorted(metric_quantile_buckets.items())
        },
    }


def _recommended_patch(summary: dict[str, Any]) -> dict[str, Any]:
    role_counts = summary.get("role_counts") or {}
    distinct = summary.get("approx_distinct_buckets") or {}
    null_rates = summary.get("null_rate_buckets") or {}
    quantiles = summary.get("metric_quantile_buckets") or {}
    return {
        "source_summary": {
            "column_profile_summary": summary,
        },
        "column_profile_calibration": {
            "id_distinct_buckets": distinct.get("id", {}),
            "dimension_distinct_buckets": _merge_counters(
                distinct.get("categorical", {}),
                distinct.get("string", {}),
            ),
            "metric_distinct_buckets": distinct.get("metric", {}),
            "metric_null_rate_buckets": null_rates.get("metric", {}),
            "metric_quantile_buckets": quantiles,
        },
        "wide_columns": {
            "dimension_columns": max(12, int(role_counts.get("categorical", 0)) + int(role_counts.get("string", 0))),
            "metric_columns": max(16, int(role_counts.get("metric", 0))),
        },
    }


def _merge_counters(*items: dict[str, int]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in items:
        for key, value in item.items():
            counter[str(key)] += int(value)
    return {key: counter[key] for key in sorted(counter)}


def _ident(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _to_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(float(value))


def _type_family(dtype: str) -> str:
    lower = dtype.lower()
    if "string" in lower or "enum" in lower:
        return "string"
    if "decimal" in lower:
        return "decimal"
    if "float" in lower:
        return "float"
    if "int" in lower:
        return "int"
    if "date" in lower or "time" in lower:
        return "temporal"
    return "other"


def _count_bucket(value: int) -> str:
    limits = [
        (0, "0"),
        (10, "1_10"),
        (100, "10_100"),
        (1_000, "100_1k"),
        (10_000, "1k_10k"),
        (100_000, "10k_100k"),
        (1_000_000, "100k_1m"),
        (10_000_000, "1m_10m"),
    ]
    if value <= 0:
        return "0"
    for limit, label in limits[1:]:
        if value <= limit:
            return label
    return "10m_plus"


def _rate_bucket(value: float) -> str:
    if value <= 0:
        return "0"
    if value <= 0.001:
        return "0_0.1pct"
    if value <= 0.01:
        return "0.1_1pct"
    if value <= 0.05:
        return "1_5pct"
    if value <= 0.2:
        return "5_20pct"
    if value <= 0.5:
        return "20_50pct"
    return "50_100pct"


def _numeric_bucket(value: Any) -> str:
    try:
        number = abs(float(value))
    except (TypeError, ValueError):
        return "unknown"
    if number == 0:
        return "0"
    if number < 1:
        return "0_1"
    if number < 10:
        return "1_10"
    if number < 100:
        return "10_100"
    if number < 1_000:
        return "100_1k"
    if number < 1_000_000:
        return "1k_1m"
    if number < 1_000_000_000:
        return "1m_1b"
    return "1b_plus"
