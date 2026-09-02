"""Synthetic wide-table dataset generation."""

from __future__ import annotations

import bisect
import copy
import csv
import gzip
import json
import math
import random
import shutil
import time
from dataclasses import dataclass
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

from .io import ensure_dir, write_json


OFFICIAL_SCALES = ("1", "10", "100", "1000")
DEFAULT_TABLE_NAME = "events_wide_table"


@dataclass(frozen=True)
class DatasetResult:
    output_dir: Path
    table_name: str
    format: str
    target_bytes: int
    actual_data_bytes: int
    row_count: int
    file_count: int
    completed_scale: bool


@dataclass
class _FileState:
    path: Path
    handle: Any
    bytes_written: int


class _WeightedSampler:
    def __init__(self, weights: list[float]) -> None:
        total = sum(max(0.0, w) for w in weights)
        if total <= 0:
            raise ValueError("weights must contain at least one positive value")
        running = 0.0
        self.cumulative: list[float] = []
        for weight in weights:
            running += max(0.0, weight) / total
            self.cumulative.append(running)
        self.cumulative[-1] = 1.0

    def index(self, rng: random.Random) -> int:
        return bisect.bisect_left(self.cumulative, rng.random())


class _ZipfSampler:
    def __init__(self, cardinality: int, *, alpha: float, head_size: int, head_mass: float) -> None:
        self.cardinality = max(1, int(cardinality))
        self.head_size = max(1, min(int(head_size), self.cardinality))
        self.head_mass = min(0.99, max(0.01, float(head_mass)))
        weights = [1.0 / (rank**alpha) for rank in range(1, self.head_size + 1)]
        self.head_sampler = _WeightedSampler(weights)

    def value(self, rng: random.Random) -> int:
        if rng.random() < self.head_mass or self.head_size >= self.cardinality:
            return self.head_sampler.index(rng) + 1
        return rng.randint(self.head_size + 1, self.cardinality)


class _DatasetRowGenerator:
    def __init__(self, spec: dict[str, Any], seed: int) -> None:
        self.spec = spec
        self.rng = random.Random(seed)
        temporal = spec.get("temporal", {})
        self.start_date = date.fromisoformat(str(temporal.get("start_date", "2024-01-01")))
        self.days = max(1, int(temporal.get("days", 30)))
        self.date_sampler = _WeightedSampler(self._date_weights(temporal))
        self.hour_sampler = _WeightedSampler([float(v) for v in temporal.get("hour_weights", [1.0] * 24)])
        ids = spec.get("ids", {})
        self.user_sampler = _ZipfSampler(
            int(ids.get("user_cardinality", 50_000_000)),
            alpha=float(ids.get("user_zipf_alpha", 1.08)),
            head_size=int(ids.get("user_head_size", 100_000)),
            head_mass=float(ids.get("user_head_mass", 0.35)),
        )
        self.item_sampler = _ZipfSampler(
            int(ids.get("item_cardinality", 10_000_000)),
            alpha=float(ids.get("item_zipf_alpha", 1.12)),
            head_size=int(ids.get("item_head_size", 50_000)),
            head_mass=float(ids.get("item_head_mass", 0.42)),
        )
        self.author_sampler = _ZipfSampler(
            int(ids.get("author_cardinality", 2_000_000)),
            alpha=float(ids.get("author_zipf_alpha", 1.10)),
            head_size=int(ids.get("author_head_size", 20_000)),
            head_mass=float(ids.get("author_head_mass", 0.38)),
        )
        self.categorical_samplers = {
            name: _ChoiceSampler(values)
            for name, values in (spec.get("categorical") or {}).items()
        }
        self.schema = build_dataset_schema(spec)
        self.columns = [column["name"] for column in self.schema]
        self.metric_columns = [c for c in self.columns if c.startswith("metric_")]
        self.dim_columns = [c for c in self.columns if c.startswith("dim_")]
        self.flag_columns = [c for c in self.columns if c.startswith("flag_")]

    def _date_weights(self, temporal: dict[str, Any]) -> list[float]:
        weekly = [float(v) for v in temporal.get("weekly_weights", [1.0] * 7)]
        recency_decay = float(temporal.get("recency_decay", 0.04))
        weights = []
        for idx in range(self.days):
            weekday_weight = weekly[idx % len(weekly)]
            recency_weight = math.exp(recency_decay * idx)
            weights.append(weekday_weight * recency_weight)
        return weights

    def sample_date(self) -> str:
        day = self.date_sampler.index(self.rng)
        return (self.start_date + timedelta(days=day)).isoformat()

    def row_values(self, *, event_date: str | None = None) -> list[Any]:
        rng = self.rng
        event_date = event_date or self.sample_date()
        event_hour = self.hour_sampler.index(rng)
        user_id = self.user_sampler.value(rng)
        item_id = self.item_sampler.value(rng)
        author_id = self.author_sampler.value(rng)
        device_id = f"dev_{rng.randrange(1, 120_000_000):09d}"
        session_id = f"sess_{rng.randrange(1, 2_000_000_000):010d}"
        scene_id = self._choice("scene_id")
        strategy_id = self._choice("strategy_id")
        experiment_id = self._choice("experiment_id")
        bucket_id = rng.randrange(0, 100)
        region_id = self._choice("region_id")
        city_id = region_id * 1000 + rng.randrange(1, 200)
        app_version = self._choice("app_version")
        os_name = self._choice("os_name")
        device_tier = self._choice("device_tier")
        content_type = self._choice("content_type")
        traffic_source = self._choice("traffic_source")
        is_ad = 1 if rng.random() < float(self.spec.get("metrics", {}).get("ad_row_rate", 0.14)) else 0
        campaign_id = self._sparse_id(20_000, 0.18 if is_ad else 0.02)
        shop_id = self._sparse_id(5_000_000, 0.35 if is_ad else 0.08)
        order_id = self._sparse_id(3_000_000_000, 0.08 if is_ad else 0.015)

        head_item = item_id <= int(self.spec.get("ids", {}).get("item_head_size", 50_000))
        engagement_multiplier = 2.8 if head_item else 0.75
        show_cnt = max(1, int(rng.lognormvariate(1.1, 1.1) * (2.0 if head_item else 1.0)))
        click_cnt = self._rate_count(show_cnt, 0.045 * engagement_multiplier)
        play_cnt = self._rate_count(show_cnt, 0.62 * engagement_multiplier)
        like_cnt = self._rate_count(play_cnt, 0.018 * engagement_multiplier)
        comment_cnt = self._rate_count(play_cnt, 0.0035 * engagement_multiplier)
        share_cnt = self._rate_count(play_cnt, 0.0025 * engagement_multiplier)
        follow_cnt = self._rate_count(play_cnt, 0.0018 * engagement_multiplier)
        finish_cnt = self._rate_count(play_cnt, 0.19 * engagement_multiplier)
        watch_duration_ms = int(play_cnt * rng.lognormvariate(9.0, 0.8)) if play_cnt else 0
        revenue_micros = self._money_metric(click_cnt, multiplier=1.8 if is_ad else 0.35)
        cost_micros = int(revenue_micros * rng.uniform(0.18, 0.72)) if revenue_micros else 0
        quality_score = round(min(1.0, max(0.0, rng.betavariate(2.5 if head_item else 1.4, 2.0))), 6)
        rank_score = round(min(1.0, max(0.0, rng.betavariate(2.2, 3.8) * (1.2 if head_item else 0.9))), 6)
        attrs_json = json.dumps(
            {
                "scene": f"scene_{scene_id:03d}",
                "strategy_family": f"strategy_{strategy_id // 10:03d}",
                "experiment": f"exp_{experiment_id:04d}",
                "content_type": content_type,
                "head_item": head_item,
                "commercial": bool(is_ad),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        tags_array = json.dumps([f"tag_{(item_id + offset * 17) % 2048:04d}" for offset in range(3)], separators=(",", ":"))
        scores_array = json.dumps([round(rank_score * rng.uniform(0.75, 1.25), 6) for _ in range(4)], separators=(",", ":"))

        row: dict[str, Any] = {
            "event_date": event_date,
            "event_hour": event_hour,
            "user_id": user_id,
            "device_id": device_id,
            "item_id": item_id,
            "author_id": author_id,
            "session_id": session_id,
            "scene_id": scene_id,
            "strategy_id": strategy_id,
            "experiment_id": experiment_id,
            "bucket_id": bucket_id,
            "region_id": region_id,
            "city_id": city_id,
            "app_version": app_version,
            "os_name": os_name,
            "device_tier": device_tier,
            "content_type": content_type,
            "traffic_source": traffic_source,
            "is_ad": is_ad,
            "campaign_id": campaign_id,
            "shop_id": shop_id,
            "order_id": order_id,
            "show_cnt": show_cnt,
            "click_cnt": click_cnt,
            "play_cnt": play_cnt,
            "like_cnt": like_cnt,
            "comment_cnt": comment_cnt,
            "share_cnt": share_cnt,
            "follow_cnt": follow_cnt,
            "finish_cnt": finish_cnt,
            "watch_duration_ms": watch_duration_ms,
            "revenue_micros": revenue_micros,
            "cost_micros": cost_micros,
            "quality_score": quality_score,
            "rank_score": rank_score,
            "attrs_json": attrs_json,
            "tags_array": tags_array,
            "scores_array": scores_array,
        }
        for idx, name in enumerate(self.dim_columns, start=1):
            row[name] = self._dimension_value(idx, user_id, item_id, strategy_id)
        for idx, name in enumerate(self.flag_columns, start=1):
            row[name] = 1 if rng.random() < (0.08 + (idx % 5) * 0.03) else 0
        for idx, name in enumerate(self.metric_columns, start=1):
            base = rng.lognormvariate(1.0 + (idx % 7) * 0.12, 1.0)
            row[name] = round(base * (2.0 if head_item else 0.8), 6)
        return [row[name] for name in self.columns]

    def _choice(self, name: str) -> Any:
        sampler = self.categorical_samplers.get(name)
        if sampler is None:
            raise KeyError(f"Missing categorical distribution for {name}")
        return sampler.value(self.rng)

    def _sparse_id(self, cardinality: int, probability: float) -> int:
        if self.rng.random() > probability:
            return 0
        return self.rng.randint(1, cardinality)

    def _rate_count(self, base: int, rate: float) -> int:
        if base <= 0:
            return 0
        noisy_rate = min(1.0, max(0.0, rate * self.rng.uniform(0.35, 1.8)))
        return min(base, int(base * noisy_rate + self.rng.random()))

    def _money_metric(self, base: int, *, multiplier: float) -> int:
        if base <= 0 or self.rng.random() < 0.55:
            return 0
        return int(base * multiplier * self.rng.lognormvariate(10.2, 1.0))

    def _dimension_value(self, idx: int, user_id: int, item_id: int, strategy_id: int) -> int:
        modulus = 10_000 + idx * 997
        return (user_id * (idx + 11) + item_id * (idx + 17) + strategy_id) % modulus


class _ChoiceSampler:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        if not items:
            raise ValueError("categorical distribution cannot be empty")
        self.values = [item["value"] for item in items]
        self.sampler = _WeightedSampler([float(item.get("weight", 1.0)) for item in items])

    def value(self, rng: random.Random) -> Any:
        return self.values[self.sampler.index(rng)]


def parse_size(size: str, *, default_unit: str = "B") -> int:
    text = size.strip().upper()
    if not text:
        raise ValueError("scale cannot be empty")
    units = {
        "": 1,
        "B": 1,
        "K": 1 << 10,
        "KB": 1 << 10,
        "M": 1 << 20,
        "MB": 1 << 20,
        "G": 1 << 30,
        "GB": 1 << 30,
        "T": 1 << 40,
        "TB": 1 << 40,
    }
    default_unit = default_unit.strip().upper()
    if default_unit not in units:
        raise ValueError(f"Invalid default unit: {default_unit!r}")
    for unit in sorted(units, key=len, reverse=True):
        if unit and text.endswith(unit):
            number = text[: -len(unit)]
            break
    else:
        unit = default_unit
        number = text
    try:
        value = float(number)
    except ValueError as exc:
        raise ValueError(f"Invalid scale: {size!r}") from exc
    if value <= 0:
        raise ValueError("scale must be positive")
    return int(value * units[unit])


def format_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{num_bytes} B"


def load_distribution_spec(path: str | Path) -> dict[str, Any]:
    spec_path = Path(path)
    opener = gzip.open if spec_path.suffix == ".gz" else open
    with opener(spec_path, "rt", encoding="utf-8") as f:
        return json.load(f)


def build_dataset_schema(spec: dict[str, Any]) -> list[dict[str, str]]:
    schema = [
        ("event_date", "date", "Partition date."),
        ("event_hour", "uint8", "Hour of day."),
        ("user_id", "uint64", "Synthetic user key."),
        ("device_id", "string", "Synthetic device key."),
        ("item_id", "uint64", "Synthetic content/item key."),
        ("author_id", "uint64", "Synthetic author key."),
        ("session_id", "string", "Synthetic session key."),
        ("scene_id", "uint16", "Business scene identifier."),
        ("strategy_id", "uint16", "Recommendation strategy identifier."),
        ("experiment_id", "uint16", "Experiment identifier."),
        ("bucket_id", "uint8", "Experiment bucket."),
        ("region_id", "uint16", "Region identifier."),
        ("city_id", "uint32", "City identifier correlated with region."),
        ("app_version", "string", "Application version."),
        ("os_name", "string", "Operating system."),
        ("device_tier", "string", "Device tier."),
        ("content_type", "string", "Content category."),
        ("traffic_source", "string", "Traffic source."),
        ("is_ad", "uint8", "Commercial row flag."),
        ("campaign_id", "uint32", "Synthetic campaign key, 0 when absent."),
        ("shop_id", "uint64", "Synthetic shop key, 0 when absent."),
        ("order_id", "uint64", "Synthetic order key, 0 when absent."),
        ("show_cnt", "uint32", "Exposure count."),
        ("click_cnt", "uint32", "Click count."),
        ("play_cnt", "uint32", "Play count."),
        ("like_cnt", "uint32", "Like count."),
        ("comment_cnt", "uint32", "Comment count."),
        ("share_cnt", "uint32", "Share count."),
        ("follow_cnt", "uint32", "Follow count."),
        ("finish_cnt", "uint32", "Completion count."),
        ("watch_duration_ms", "uint64", "Watch duration."),
        ("revenue_micros", "uint64", "Commercial revenue in micros."),
        ("cost_micros", "uint64", "Commercial cost in micros."),
        ("quality_score", "float64", "Content quality score."),
        ("rank_score", "float64", "Ranking score."),
        ("attrs_json", "string", "JSON-like sparse attributes."),
        ("tags_array", "string", "JSON-encoded tag array."),
        ("scores_array", "string", "JSON-encoded score array."),
    ]
    wide = spec.get("wide_columns", {})
    for idx in range(1, int(wide.get("dimension_columns", 24)) + 1):
        schema.append((f"dim_{idx:03d}", "uint32", "Synthetic medium-cardinality dimension."))
    for idx in range(1, int(wide.get("flag_columns", 12)) + 1):
        schema.append((f"flag_{idx:03d}", "uint8", "Synthetic sparse binary flag."))
    for idx in range(1, int(wide.get("metric_columns", 32)) + 1):
        schema.append((f"metric_{idx:03d}", "float64", "Synthetic numeric metric."))
    return [{"name": name, "type": dtype, "description": description} for name, dtype, description in schema]


def generate_dataset(
    *,
    scale: str,
    output_dir: str | Path | None,
    spec_path: str | Path,
    fmt: str = "csv",
    seed: int = 1,
    target_file_size: str = "128M",
    max_rows: int | None = None,
    compression: str = "snappy",
    start_date: str | None = None,
    days: int | None = None,
) -> DatasetResult:
    target_bytes = parse_size(scale, default_unit="GB")
    target_file_bytes = parse_size(target_file_size)
    if target_file_bytes <= 0:
        raise ValueError("target_file_size must be positive")
    fmt = fmt.lower()
    if fmt not in {"csv", "parquet"}:
        raise ValueError("format must be one of: csv, parquet")
    spec = _apply_temporal_overrides(load_distribution_spec(spec_path), start_date=start_date, days=days)
    out_path = Path(output_dir) if output_dir is not None else Path("artifacts/datasets") / f"scale_{scale}"
    if out_path.exists():
        shutil.rmtree(out_path)
    ensure_dir(out_path)

    if fmt == "csv":
        result = _generate_csv_dataset(
            spec=spec,
            scale=scale,
            target_bytes=target_bytes,
            target_file_bytes=target_file_bytes,
            output_dir=out_path,
            seed=seed,
            max_rows=max_rows,
        )
    else:
        result = _generate_parquet_dataset(
            spec=spec,
            scale=scale,
            target_bytes=target_bytes,
            output_dir=out_path,
            seed=seed,
            max_rows=max_rows,
            compression=compression,
        )
    _write_dataset_metadata(out_path, spec_path=spec_path, spec=spec, result=result, scale=scale)
    return result


def inspect_dataset(path: str | Path) -> dict[str, Any]:
    root = Path(path)
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    files = [p for p in root.rglob("*") if p.is_file()]
    data_files = [p for p in files if p.suffix.lower() in {".csv", ".parquet"}]
    return {
        "path": str(root),
        "file_count": len(data_files),
        "actual_data_bytes": sum(p.stat().st_size for p in data_files),
        "human_actual_data_size": format_size(sum(p.stat().st_size for p in data_files)),
        "has_manifest": False,
    }


def _apply_temporal_overrides(spec: dict[str, Any], *, start_date: str | None, days: int | None) -> dict[str, Any]:
    updated = copy.deepcopy(spec)
    temporal = updated.setdefault("temporal", {})
    if start_date is not None:
        # Validate early so CLI users get a clear error before files are written.
        date.fromisoformat(start_date)
        temporal["start_date"] = start_date
    if days is not None:
        if days <= 0:
            raise ValueError("days must be positive")
        temporal["days"] = int(days)
    return updated


def _generate_csv_dataset(
    *,
    spec: dict[str, Any],
    scale: str,
    target_bytes: int,
    target_file_bytes: int,
    output_dir: Path,
    seed: int,
    max_rows: int | None,
) -> DatasetResult:
    generator = _DatasetRowGenerator(spec, seed)
    table_dir = output_dir / DEFAULT_TABLE_NAME
    columns = generator.columns
    header = _csv_line(columns)
    header_bytes = len(header.encode("utf-8"))
    states: dict[str, _FileState] = {}
    part_counts: dict[str, int] = {}
    files: list[Path] = []
    actual_bytes = 0
    row_count = 0

    try:
        while actual_bytes < target_bytes:
            if max_rows is not None and row_count >= max_rows:
                break
            event_date = generator.sample_date()
            values = generator.row_values(event_date=event_date)
            line = _csv_line(values)
            line_bytes = len(line.encode("utf-8"))
            state = states.get(event_date)
            if state is None or (state.bytes_written > header_bytes and state.bytes_written + line_bytes > target_file_bytes):
                if state is not None:
                    state.handle.close()
                state = _open_csv_part(table_dir, event_date, part_counts, header)
                states[event_date] = state
                files.append(state.path)
                actual_bytes += header_bytes
            state.handle.write(line)
            state.bytes_written += line_bytes
            actual_bytes += line_bytes
            row_count += 1
    finally:
        for state in states.values():
            state.handle.close()

    completed_scale = actual_bytes >= target_bytes
    _write_file_index(output_dir, files)
    return DatasetResult(
        output_dir=output_dir,
        table_name=DEFAULT_TABLE_NAME,
        format="csv",
        target_bytes=target_bytes,
        actual_data_bytes=actual_bytes,
        row_count=row_count,
        file_count=len(files),
        completed_scale=completed_scale,
    )


def _generate_parquet_dataset(
    *,
    spec: dict[str, Any],
    scale: str,
    target_bytes: int,
    output_dir: Path,
    seed: int,
    max_rows: int | None,
    compression: str,
) -> DatasetResult:
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.parquet as pq  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Parquet generation requires pyarrow. Install it before using --format parquet.") from exc

    generator = _DatasetRowGenerator(spec, seed)
    table_dir = output_dir / DEFAULT_TABLE_NAME
    batch_rows = max(1000, int(spec.get("parquet", {}).get("rows_per_file", 50_000)))
    actual_bytes = 0
    row_count = 0
    file_count = 0
    files: list[Path] = []
    while actual_bytes < target_bytes:
        if max_rows is not None and row_count >= max_rows:
            break
        event_date = generator.sample_date()
        remaining_rows = batch_rows if max_rows is None else min(batch_rows, max_rows - row_count)
        if remaining_rows <= 0:
            break
        rows = [dict(zip(generator.columns, generator.row_values(event_date=event_date))) for _ in range(remaining_rows)]
        partition_dir = ensure_dir(table_dir / f"event_date={event_date}")
        path = partition_dir / f"part-{file_count:05d}.parquet"
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, path, compression=compression)
        file_size = path.stat().st_size
        actual_bytes += file_size
        row_count += len(rows)
        file_count += 1
        files.append(path)

    completed_scale = actual_bytes >= target_bytes
    _write_file_index(output_dir, files)
    return DatasetResult(
        output_dir=output_dir,
        table_name=DEFAULT_TABLE_NAME,
        format="parquet",
        target_bytes=target_bytes,
        actual_data_bytes=actual_bytes,
        row_count=row_count,
        file_count=file_count,
        completed_scale=completed_scale,
    )


def _open_csv_part(table_dir: Path, event_date: str, part_counts: dict[str, int], header: str) -> _FileState:
    part_id = part_counts.get(event_date, 0)
    part_counts[event_date] = part_id + 1
    partition_dir = ensure_dir(table_dir / f"event_date={event_date}")
    path = partition_dir / f"part-{part_id:05d}.csv"
    handle = path.open("w", encoding="utf-8", newline="")
    handle.write(header)
    return _FileState(path=path, handle=handle, bytes_written=len(header.encode("utf-8")))


def _csv_line(values: list[Any]) -> str:
    buf = StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(values)
    return buf.getvalue()


def _write_file_index(output_dir: Path, files: list[Path]) -> None:
    rows = [{"path": str(path.relative_to(output_dir)), "bytes": path.stat().st_size} for path in sorted(files)]
    write_json(output_dir / "file_index.json", {"files": rows})


def _write_dataset_metadata(
    output_dir: Path,
    *,
    spec_path: str | Path,
    spec: dict[str, Any],
    result: DatasetResult,
    scale: str,
) -> None:
    schema = build_dataset_schema(spec)
    temporal = spec.get("temporal", {})
    start = date.fromisoformat(str(temporal.get("start_date", "2024-01-01")))
    days = max(1, int(temporal.get("days", 30)))
    end = start + timedelta(days=days - 1)
    write_json(output_dir / "table_schema.json", {"table": result.table_name, "columns": schema}, pretty=True)
    write_json(
        output_dir / "manifest.json",
        {
            "benchmark": "megabench",
            "artifact": "synthetic_dataset",
            "format_version": 1,
            "scale": scale,
            "official_scales": list(OFFICIAL_SCALES),
            "table": result.table_name,
            "format": result.format,
            "target_bytes": result.target_bytes,
            "actual_data_bytes": result.actual_data_bytes,
            "human_target_size": format_size(result.target_bytes),
            "human_actual_data_size": format_size(result.actual_data_bytes),
            "row_count": result.row_count,
            "file_count": result.file_count,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "days": days,
            "completed_scale": result.completed_scale,
            "distribution_spec": str(spec_path),
            "generated_at_unix": int(time.time()),
        },
        pretty=True,
    )
    write_json(output_dir / "distribution_spec_used.json", spec, pretty=True)
