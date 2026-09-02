"""Command line interface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .dataset import OFFICIAL_SCALES, format_size, generate_dataset, inspect_dataset
from .generate import PROFILE_NAMES, generate_workload, normalize_profile
from .mine import build_public_artifacts
from .profile import DEFAULT_PROFILE_OUTPUT, profile_columns


DEFAULT_TRACE_PATH = Path("data/private")
DEFAULT_PUBLIC_DIR = Path("data/public")
DEFAULT_QUERY_STREAM_DIR = Path("artifacts/query_streams")
DEFAULT_DATASET_DIR = Path("artifacts/datasets")
DEFAULT_DISTRIBUTION_SPEC = DEFAULT_PUBLIC_DIR / "synthetic_dataset" / "distribution_spec.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="megabench")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build public artifacts from private raw_data.jsonl traces.")
    build.add_argument(
        "--trace",
        default=str(DEFAULT_TRACE_PATH),
        help=f"Advanced: private trace file, directory, or glob. Default: {DEFAULT_TRACE_PATH}",
    )
    build.add_argument(
        "--output",
        default=str(DEFAULT_PUBLIC_DIR),
        help=f"Advanced: directory for public artifacts. Default: {DEFAULT_PUBLIC_DIR}",
    )
    build.add_argument("--limit", type=int, default=None, help="Maximum number of source records to scan.")
    build.add_argument("--sample-size", type=int, default=20000, help="Advanced: maximum records written to query_sample.jsonl.")
    build.add_argument(
        "--min-pattern-count",
        type=int,
        default=3,
        help="Privacy filter: keep only query patterns seen at least this many times.",
    )
    build.add_argument("--keep-rare", action="store_true", help="Keep rare templates. Not recommended for public release.")

    generate = subparsers.add_parser("generate", help="Generate synthetic workload rows from public templates.")
    generate.add_argument(
        "--model-dir",
        default=str(DEFAULT_PUBLIC_DIR),
        help=f"Directory containing templates from build. Default: {DEFAULT_PUBLIC_DIR}",
    )
    generate.add_argument(
        "--output",
        default=None,
        help=f"Output JSONL path. Default: {DEFAULT_QUERY_STREAM_DIR}/<profile>.jsonl",
    )
    generate.add_argument("--num", type=int, default=1000, help="Number of synthetic query rows to generate.")
    generate.add_argument("--seed", type=int, default=1, help="Random seed.")
    generate.add_argument(
        "--profile",
        choices=PROFILE_NAMES,
        default="standard_workload",
        help=f"Workload mix profile. Public profiles: {', '.join(PROFILE_NAMES)}.",
    )

    show = subparsers.add_parser("show-manifest", help="Print a compact manifest summary.")
    show.add_argument("--model-dir", default=str(DEFAULT_PUBLIC_DIR), help=f"Directory containing benchmark_manifest.json. Default: {DEFAULT_PUBLIC_DIR}")

    profile = subparsers.add_parser("profile", help="Maintainer-only private profiling tools.")
    profile_subparsers = profile.add_subparsers(dest="profile_command", required=True)

    profile_columns_parser = profile_subparsers.add_parser(
        "columns",
        help="Profile coarse column distributions with guarded ClickHouse HTTP queries.",
    )
    profile_columns_parser.add_argument("--http-url", required=True, help="ClickHouse HTTP endpoint URL.")
    profile_columns_parser.add_argument("--user", default=None, help="ClickHouse user.")
    profile_columns_parser.add_argument("--password", default=None, help="ClickHouse password. Prefer --password-env.")
    profile_columns_parser.add_argument("--password-env", default=None, help="Environment variable containing password.")
    profile_columns_parser.add_argument("--trace", default=None, help="Private raw_data.jsonl trace root used to select top tables.")
    profile_columns_parser.add_argument("--trace-limit", type=int, default=50_000, help="Maximum private trace rows scanned to select top tables.")
    profile_columns_parser.add_argument("--table", action="append", default=[], help="Table to profile as database.table. Repeatable.")
    profile_columns_parser.add_argument("--where", default=None, help="Optional WHERE clause for profiled source tables.")
    profile_columns_parser.add_argument("--rows-per-table", type=int, default=10_000, help="Rows sampled per table via LIMIT.")
    profile_columns_parser.add_argument("--max-tables", type=int, default=5, help="Maximum tables selected from --trace/--table.")
    profile_columns_parser.add_argument("--max-columns-per-role", type=int, default=4, help="Maximum columns per inferred role per table.")
    profile_columns_parser.add_argument("--max-execution-time", type=int, default=10, help="Server-side max_execution_time.")
    profile_columns_parser.add_argument("--max-bytes-to-read", type=int, default=1 << 30, help="Server-side max_bytes_to_read.")
    profile_columns_parser.add_argument("--timeout", type=int, default=15, help="Client-side HTTP timeout in seconds.")
    profile_columns_parser.add_argument("--query-prefix", default="megabench_profile", help="Prefix for generated query_id values.")
    profile_columns_parser.add_argument("--output", default=str(DEFAULT_PROFILE_OUTPUT), help=f"Output path. Default: {DEFAULT_PROFILE_OUTPUT}")

    dataset = subparsers.add_parser("dataset", help="Generate or inspect synthetic wide-table datasets.")
    dataset_subparsers = dataset.add_subparsers(dest="dataset_command", required=True)

    dataset_generate = dataset_subparsers.add_parser(
        "generate",
        help="Generate a synthetic external-table wide dataset.",
    )
    dataset_generate.add_argument(
        "--scale",
        required=True,
        help=f"Target data size in GiB. Official scales: {', '.join(OFFICIAL_SCALES)}.",
    )
    dataset_generate.add_argument(
        "--output",
        default=None,
        help=f"Output directory. Default: {DEFAULT_DATASET_DIR}/scale_<scale>.",
    )
    dataset_generate.add_argument(
        "--spec",
        default=str(DEFAULT_DISTRIBUTION_SPEC),
        help=f"Distribution spec path. Default: {DEFAULT_DISTRIBUTION_SPEC}",
    )
    dataset_generate.add_argument("--format", choices=["csv", "parquet"], default="csv", help="Output file format.")
    dataset_generate.add_argument("--seed", type=int, default=1, help="Random seed.")
    dataset_generate.add_argument("--start-date", default=None, help="Override dataset start date, for example 2024-01-01.")
    dataset_generate.add_argument("--days", type=int, default=None, help="Override number of event_date partitions.")
    dataset_generate.add_argument("--target-file-size", default="128M", help="Approximate target size per CSV part.")
    dataset_generate.add_argument("--max-rows", type=int, default=None, help="Debug cap. Stops early if set.")
    dataset_generate.add_argument("--compression", default="snappy", help="Parquet compression when --format parquet.")

    inspect_parser = dataset_subparsers.add_parser("inspect", help="Print dataset manifest or scan file sizes.")
    inspect_parser.add_argument("path", help="Generated dataset directory.")

    args = parser.parse_args(argv)

    if args.command == "build":
        result = build_public_artifacts(
            trace_path=args.trace,
            output_dir=args.output,
            limit=args.limit,
            min_pattern_count=args.min_pattern_count,
            sample_size=args.sample_size,
            keep_rare=args.keep_rare,
        )
        print(
            "Built MegaBench artifacts: "
            f"{result.public_record_count}/{result.private_record_count} records retained, "
            f"{result.template_count} templates, output={result.out_dir}"
        )
        return 0

    if args.command == "generate":
        profile = normalize_profile(args.profile)
        output = Path(args.output) if args.output else DEFAULT_QUERY_STREAM_DIR / f"{profile}.jsonl"
        count = generate_workload(
            model_dir=args.model_dir,
            out_path=output,
            num_queries=args.num,
            seed=args.seed,
            profile=profile,
        )
        print(f"Generated {count} synthetic workload rows at {output}")
        return 0

    if args.command == "show-manifest":
        manifest_path = Path(args.model_dir) / "benchmark_manifest.json"
        if not manifest_path.exists():
            manifest_path = Path(args.model_dir) / "manifest.json"
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    if args.command == "profile":
        if args.profile_command == "columns":
            try:
                result = profile_columns(
                    http_url=args.http_url,
                    output_path=args.output,
                    user=args.user,
                    password=args.password,
                    password_env=args.password_env,
                    trace_path=args.trace,
                    trace_limit=args.trace_limit,
                    tables=args.table,
                    where=args.where,
                    rows_per_table=args.rows_per_table,
                    max_tables=args.max_tables,
                    max_columns_per_role=args.max_columns_per_role,
                    max_execution_time=args.max_execution_time,
                    max_bytes_to_read=args.max_bytes_to_read,
                    timeout=args.timeout,
                    query_prefix=args.query_prefix,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                parser.exit(1, f"error: {exc}\n")
            print(
                "Profiled column distributions: "
                f"tables={result.table_count}, columns={result.column_count}, "
                f"queries={result.query_count}, kill_attempts={result.killed_queries}, "
                f"output={result.output_path}"
            )
            return 0

    if args.command == "dataset":
        if args.dataset_command == "generate":
            output = Path(args.output) if args.output else DEFAULT_DATASET_DIR / f"scale_{args.scale}"
            try:
                result = generate_dataset(
                    scale=args.scale,
                    output_dir=output,
                    spec_path=args.spec,
                    fmt=args.format,
                    seed=args.seed,
                    target_file_size=args.target_file_size,
                    max_rows=args.max_rows,
                    compression=args.compression,
                    start_date=args.start_date,
                    days=args.days,
                )
            except (RuntimeError, ValueError) as exc:
                parser.exit(1, f"error: {exc}\n")
            print(
                "Generated MegaBench dataset: "
                f"rows={result.row_count}, files={result.file_count}, "
                f"size={format_size(result.actual_data_bytes)}, output={result.output_dir}"
            )
            if not result.completed_scale:
                print("Generation stopped before reaching target scale because --max-rows was reached.")
            return 0
        if args.dataset_command == "inspect":
            print(json.dumps(inspect_dataset(args.path), indent=2, sort_keys=True))
            return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
