"""Distribution validation for private-vs-public workload artifacts."""

from __future__ import annotations

from typing import Any

from .util import js_divergence


DEFAULT_HISTOGRAMS = [
    "label",
    "query_type",
    "query_length_bucket",
    "read_bytes",
    "lake_read_size",
    "lake_read_files",
    "query_duration_ms",
    "event_hour",
]


def compare_summaries(
    private_summary: dict[str, Any],
    public_summary: dict[str, Any],
    histogram_names: list[str] | None = None,
) -> dict[str, Any]:
    names = histogram_names or DEFAULT_HISTOGRAMS
    private_hists = private_summary.get("histograms") or {}
    public_hists = public_summary.get("histograms") or {}
    comparisons = {}
    for name in names:
        left = private_hists.get(name) or {}
        right = public_hists.get(name) or {}
        comparisons[name] = {
            "js_divergence": round(js_divergence(left, right), 6),
            "private_cardinality": len(left),
            "public_cardinality": len(right),
        }

    private_count = int(private_summary.get("record_count") or 0)
    public_count = int(public_summary.get("record_count") or 0)
    return {
        "private_record_count": private_count,
        "public_record_count": public_count,
        "retention_ratio": round(public_count / private_count, 6) if private_count else 0.0,
        "histogram_comparisons": comparisons,
    }


def render_markdown_report(
    comparison: dict[str, Any],
    *,
    min_pattern_count: int,
    source_file_count: int,
    retained_record_count: int | None = None,
    sample_size: int | None = None,
) -> str:
    lines = [
        "# MegaBench Validation Report",
        "",
        "This report compares the private source trace with the public sanitized workload artifact.",
        "Only coarse histograms and divergence values are written here.",
        "",
        "## Build Summary",
        "",
        f"- Source files: {source_file_count}",
        f"- Private records scanned: {comparison['private_record_count']}",
        f"- Public workload sample records: {comparison['public_record_count']}",
        f"- Published sample ratio: {comparison['retention_ratio']}",
        f"- Minimum query pattern count: {min_pattern_count}",
    ]
    if retained_record_count is not None:
        lines.append(f"- Retained records before sampling: {retained_record_count}")
    if sample_size is not None:
        lines.append(f"- Maximum sample size: {sample_size}")
    lines.extend(
        [
            "",
            "## Histogram Divergence",
            "",
            "| Histogram | JS divergence | Private bins | Public bins |",
            "|---|---:|---:|---:|",
        ]
    )
    for name, values in comparison["histogram_comparisons"].items():
        lines.append(
            f"| `{name}` | {values['js_divergence']:.6f} | "
            f"{values['private_cardinality']} | {values['public_cardinality']} |"
        )
    lines.extend(
        [
            "",
            "## Privacy Notes",
            "",
            "- Raw SQL text is not emitted.",
            "- Real database, table, column, user, query_id, and exception strings are not emitted.",
            "- Runtime and IO metrics are bucketed and should be treated as oracle labels, not model inputs.",
            "- Query patterns below the configured minimum count are dropped by default.",
        ]
    )
    return "\n".join(lines) + "\n"
