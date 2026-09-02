import json
import tempfile
import unittest
from pathlib import Path

from megabench.cli import main
from megabench.generate import generate_workload
from megabench.mine import build_public_artifacts


class PipelineTest(unittest.TestCase):
    def test_build_and_generate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "daily" / "data_20260701" / "raw_data.jsonl"
            root.parent.mkdir(parents=True)
            raw_path = root
            rows = [
                _row("SELECT count() FROM db_a.events WHERE dt = '2026-07-01' AND user_id = 1"),
                _row("SELECT count() FROM db_a.events WHERE dt = '2026-07-02' AND user_id = 2"),
                _row("SELECT count() FROM db_a.events WHERE dt = '2026-07-03' AND user_id = 3"),
            ]
            raw_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            output_dir = Path(tmp) / "public"
            result = build_public_artifacts(
                trace_path=Path(tmp) / "daily",
                output_dir=output_dir,
                min_pattern_count=2,
            )
            self.assertEqual(result.public_record_count, 3)
            self.assertTrue((output_dir / "templates.json.gz").exists())

            generated = Path(tmp) / "generated.jsonl"
            count = generate_workload(model_dir=output_dir, out_path=generated, num_queries=5, seed=7)
            self.assertEqual(count, 5)
            self.assertEqual(len(generated.read_text(encoding="utf-8").strip().splitlines()), 5)

    def test_rejects_ambiguous_private_partitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "daily"
            first = root / "data_20260701" / "partition" / "a" / "raw_data.jsonl"
            second = root / "data_20260701" / "partition" / "b" / "raw_data.jsonl"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_text(json.dumps(_row("SELECT 1")) + "\n", encoding="utf-8")
            second.write_text(json.dumps(_row("SELECT 2")) + "\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                build_public_artifacts(
                    trace_path=root,
                    output_dir=Path(tmp) / "public",
                    min_pattern_count=1,
                )

    def test_cli_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path.cwd()
            try:
                root = Path(tmp) / "data" / "private"
                root.mkdir(parents=True)
                raw_path = root / "raw_data.jsonl"
                rows = [
                    _row("SELECT count() FROM db_a.events WHERE dt = '2026-07-01' AND user_id = 1"),
                    _row("SELECT count() FROM db_a.events WHERE dt = '2026-07-02' AND user_id = 2"),
                    _row("SELECT count() FROM db_a.events WHERE dt = '2026-07-03' AND user_id = 3"),
                ]
                raw_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
                import os

                os.chdir(tmp)
                self.assertEqual(main(["build", "--min-pattern-count", "2"]), 0)
                self.assertTrue((Path(tmp) / "data" / "public" / "workload.jsonl").exists())
                self.assertEqual(main(["generate", "--num", "2"]), 0)
                self.assertTrue((Path(tmp) / "artifacts" / "generated_workload.jsonl").exists())
            finally:
                import os

                os.chdir(cwd)


def _row(sql: str):
    return {
        "sql": sql,
        "query_plan": "ReadFromStorage -> Filter -> Aggregating",
        "meta": {
            "query_length": len(sql),
            "query_type": 2,
            "num_tables": 1,
            "num_columns": 3,
            "event_time": "2026-07-01 13:00:00",
            "read_rows": 1000,
            "read_bytes": 12345678,
            "lake_read_size": 12345678,
            "lake_read_files": 42,
            "lake_read_partitions": 3,
            "memory_usage": 2048,
            "query_duration_ms": 3000,
            "raw_label": "normal",
        },
    }


if __name__ == "__main__":
    unittest.main()
