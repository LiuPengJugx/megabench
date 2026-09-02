"""Generate synthetic workload records from mined public templates."""

from __future__ import annotations

import json
import gzip
import random
import re
from pathlib import Path
from typing import Any

from .io import write_jsonl

PLACEHOLDER_RE = re.compile(r"\{\{(date|datetime|int|float|str)\}\}")
PROFILE_NAMES = ("standard_workload", "mega_query_heavy", "external_scan_heavy")


def generate_workload(
    *,
    model_dir: str | Path,
    out_path: str | Path,
    num_queries: int,
    seed: int = 1,
    profile: str = "standard_workload",
) -> int:
    profile = normalize_profile(profile)
    rng = random.Random(seed)
    templates = _load_templates(Path(model_dir))
    if not templates:
        raise ValueError("No templates available. Build public artifacts first.")

    weights = [_template_weight(template, profile) for template in templates]
    rows = []
    for idx in range(1, num_queries + 1):
        template = rng.choices(templates, weights=weights, k=1)[0]
        sql = _instantiate_sql(template["sql"], rng)
        label = _sample_from_hist(template.get("label_counts") or {"unknown": 1}, rng)
        rows.append(
            {
                "query_id": f"syn_{idx:08d}",
                "template_id": template["template_id"],
                "sql": sql,
                "profile": profile,
                "label": label,
                "pre_execution_features": _sample_feature_hists(template.get("pre_feature_hists") or {}, rng),
                "oracle_buckets": _sample_feature_hists(template.get("oracle_bucket_hists") or {}, rng),
            }
        )

    return write_jsonl(out_path, rows)


def _load_templates(model_dir: Path) -> list[dict[str, Any]]:
    candidates = [
        model_dir / "workload" / "query_templates.json.gz",
        model_dir / "workload" / "query_templates.json",
    ]
    for path in candidates:
        if path.exists():
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8") as f:
                data = json.load(f)
            return list(data.get("templates") or [])
    raise FileNotFoundError(f"No query template catalog found in {model_dir}")


def _template_weight(template: dict[str, Any], profile: str) -> float:
    support = max(1, int(template.get("support") or 1))
    label_counts = template.get("label_counts") or {}
    oracle_hists = template.get("oracle_bucket_hists") or {}
    weight = float(support)

    profile = normalize_profile(profile)
    if profile == "mega_query_heavy":
        mega = int(label_counts.get("mega", 0))
        weight *= 1.0 + 3.0 * mega / support
    elif profile == "external_scan_heavy":
        read_files = oracle_hists.get("lake_read_files") or {}
        read_size = oracle_hists.get("lake_read_size") or {}
        stress_bins = {"10k_100k", "100k_1m", "1m_10m", "10m_100m", "100GB_1TB", "1TB_10TB", "10TB_plus"}
        stress_hits = sum(count for name, count in {**read_files, **read_size}.items() if name in stress_bins)
        weight *= 1.0 + 2.0 * stress_hits / support
    elif profile != "standard_workload":
        raise ValueError(f"Unknown profile: {profile}")
    return max(weight, 0.1)


def normalize_profile(profile: str) -> str:
    if profile in PROFILE_NAMES:
        return profile
    else:
        valid = ", ".join(PROFILE_NAMES)
        raise ValueError(f"Unknown profile: {profile}. Expected one of: {valid}")


def _instantiate_sql(sql: str, rng: random.Random) -> str:
    counters = {"date": 0, "datetime": 0, "int": 0, "float": 0, "str": 0}

    def replace(match: re.Match[str]) -> str:
        kind = match.group(1)
        counters[kind] += 1
        idx = counters[kind]
        if kind == "date":
            return f"'2026-07-{1 + rng.randrange(28):02d}'"
        if kind == "datetime":
            return f"'2026-07-{1 + rng.randrange(28):02d} {rng.randrange(24):02d}:{rng.randrange(60):02d}:00'"
        if kind == "int":
            return str(_zipf_like_int(rng, idx))
        if kind == "float":
            return f"{rng.random() * 1000:.4f}"
        return f"'v_{rng.randrange(1, 10000):04d}'"

    return PLACEHOLDER_RE.sub(replace, sql)


def _zipf_like_int(rng: random.Random, idx: int) -> int:
    hot = [1, 2, 3, 5, 8, 13, 21, 34]
    if rng.random() < 0.7:
        return hot[rng.randrange(len(hot))] + idx - 1
    return rng.randrange(1, 10_000_000)


def _sample_feature_hists(hists: dict[str, dict[str, int]], rng: random.Random) -> dict[str, str]:
    sampled: dict[str, str] = {}
    for name, hist in hists.items():
        sampled[name] = _sample_from_hist(hist, rng)
    return sampled


def _sample_from_hist(hist: dict[str, int], rng: random.Random) -> str:
    keys = list(hist)
    weights = [max(0, int(hist[key])) for key in keys]
    if not keys or sum(weights) <= 0:
        return "unknown"
    return rng.choices(keys, weights=weights, k=1)[0]
