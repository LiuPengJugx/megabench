import json
import tempfile
import unittest
from pathlib import Path

from megabench.cli import main
from megabench.dataset import generate_dataset, inspect_dataset, parse_size


SPEC_PATH = Path(__file__).resolve().parents[1] / "data" / "public" / "synthetic_dataset" / "distribution_spec.json"


class DatasetTest(unittest.TestCase):
    def test_parse_size(self):
        self.assertEqual(parse_size("1K"), 1024)
        self.assertEqual(parse_size("1G"), 1 << 30)
        self.assertEqual(parse_size("1000G"), 1000 * (1 << 30))
        self.assertEqual(parse_size("1", default_unit="GB"), 1 << 30)

    def test_generate_csv_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dataset"
            result = generate_dataset(
                scale="0.000012",
                output_dir=out,
                spec_path=SPEC_PATH,
                fmt="csv",
                seed=3,
                target_file_size="8K",
            )
            self.assertTrue(result.completed_scale)
            self.assertGreater(result.row_count, 0)
            self.assertTrue((out / "manifest.json").exists())
            self.assertTrue((out / "table_schema.json").exists())
            self.assertTrue((out / "file_index.json").exists())
            self.assertTrue((out / "distribution_spec_used.json").exists())
            self.assertGreater(len(list((out / "events_wide_table").rglob("*.csv"))), 0)
            manifest = inspect_dataset(out)
            self.assertEqual(manifest["artifact"], "synthetic_dataset")
            self.assertEqual(manifest["table"], "events_wide_table")
            self.assertEqual(manifest["format"], "csv")
            self.assertEqual(manifest["start_date"], "2024-01-01")

    def test_cli_dataset_generate_with_date_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dataset"
            status = main(
                [
                    "dataset",
                    "generate",
                    "--scale",
                    "0.000008",
                    "--output",
                    str(out),
                    "--spec",
                    str(SPEC_PATH),
                    "--seed",
                    "5",
                    "--target-file-size",
                    "4K",
                    "--start-date",
                    "2024-06-01",
                    "--days",
                    "1",
                ]
            )
            self.assertEqual(status, 0)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["format"], "csv")
            self.assertEqual(manifest["start_date"], "2024-06-01")
            self.assertEqual(manifest["end_date"], "2024-06-01")
            self.assertEqual(manifest["days"], 1)
            self.assertGreaterEqual(manifest["actual_data_bytes"], parse_size("0.000008", default_unit="GB"))
            self.assertTrue((out / "events_wide_table" / "event_date=2024-06-01").exists())


if __name__ == "__main__":
    unittest.main()
