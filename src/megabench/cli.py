"""Command line interface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .dataset import OFFICIAL_SCALES, format_size, generate_dataset, inspect_dataset
from .generate import generate_workload
from .mine import build_public_artifacts


DEFAULT_TRACE_PATH = Path("data/private")
DEFAULT_PUBLIC_DIR = Path("data/public")
DEFAULT_GENERATED_WORKLOAD = Path("artifacts/generated_workload.jsonl")
DEFAULT_DISTRIBUTION_SPEC = DEFAULT_PUBLIC_DIR / "distribution_spec.json"


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
    build.add_argument("--sample-size", type=int, default=20000, help="Advanced: maximum records written to workload.jsonl.")
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
        default=str(DEFAULT_GENERATED_WORKLOAD),
        help=f"Output JSONL path. Default: {DEFAULT_GENERATED_WORKLOAD}",
    )
    generate.add_argument("--num", type=int, default=1000, help="Number of synthetic query rows to generate.")
    generate.add_argument("--seed", type=int, default=1, help="Random seed.")
    generate.add_argument(
        "--profile",
        choices=["balanced", "mega_heavy", "external_table_stress"],
        default="balanced",
        help="Workload mix profile.",
    )

    show = subparsers.add_parser("show-manifest", help="Print a compact manifest summary.")
    show.add_argument("--model-dir", required=True, help="Directory containing manifest.json.")

    dataset = subparsers.add_parser("dataset", help="Generate or inspect synthetic wide-table datasets.")
    dataset_subparsers = dataset.add_subparsers(dest="dataset_command", required=True)

    dataset_generate = dataset_subparsers.add_parser(
        "generate",
        help="Generate a synthetic external-table wide dataset.",
    )
    dataset_generate.add_argument(
        "--scale",
        required=True,
        help=f"Target generated data size. Official scales: {', '.join(OFFICIAL_SCALES)}.",
    )
    dataset_generate.add_argument(
        "--output",
        default=None,
        help="Output directory. Default: data/generated/<scale>.",
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

    dataset_inspect = dataset_subparsers.add_parser("inspect", help="Print dataset manifest or scan file sizes.")
    dataset_inspect.add_argument("path", help="Generated dataset directory.")

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
        count = generate_workload(
            model_dir=args.model_dir,
            out_path=args.output,
            num_queries=args.num,
            seed=args.seed,
            profile=args.profile,
        )
        print(f"Generated {count} synthetic workload rows at {Path(args.output)}")
        return 0

    if args.command == "show-manifest":
        manifest_path = Path(args.model_dir) / "manifest.json"
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    if args.command == "dataset":
        if args.dataset_command == "generate":
            output = Path(args.output) if args.output else Path("data/generated") / args.scale
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
