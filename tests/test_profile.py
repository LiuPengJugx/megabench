import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from megabench.profile import ClickHouseHTTPClient, profile_columns


class _FakeClickHouseHandler(BaseHTTPRequestHandler):
    requests = []
    kill_count = 0
    fail_next = False

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8")
        type(self).requests.append(body)
        if body.startswith("KILL QUERY"):
            type(self).kill_count += 1
            self._json({"data": [], "rows": 0})
            return
        if type(self).fail_next:
            type(self).fail_next = False
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"forced error")
            return
        if "FROM system.columns" in body:
            self._json(
                {
                    "data": [
                        {"database": "private_db", "table": "private_table", "name": "user_id", "type": "UInt64"},
                        {"database": "private_db", "table": "private_table", "name": "scene_type", "type": "String"},
                        {"database": "private_db", "table": "private_table", "name": "revenue", "type": "Float64"},
                    ],
                    "rows": 3,
                }
            )
            return
        self._json(
            {
                "data": [
                    {
                        "sampled_rows": 100,
                        "c01_uniq": 80,
                        "c01_nulls": 0,
                        "c01_topk_size": 20,
                        "c02_uniq": 8,
                        "c02_nulls": 2,
                        "c02_topk_size": 8,
                        "c03_uniq": 95,
                        "c03_nulls": 40,
                        "c03_q": [0.0, 12.5, 450.0],
                    }
                ],
                "rows": 1,
            }
        )

    def log_message(self, format, *args):  # noqa: A002
        return

    def _json(self, payload):
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class ProfileTest(unittest.TestCase):
    def setUp(self):
        _FakeClickHouseHandler.requests = []
        _FakeClickHouseHandler.kill_count = 0
        _FakeClickHouseHandler.fail_next = False
        self.server = HTTPServer(("127.0.0.1", 0), _FakeClickHouseHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)

    def test_profile_columns_redacts_raw_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "profile.json"
            result = profile_columns(
                http_url=self.url,
                output_path=output,
                tables=["private_db.private_table"],
                max_execution_time=3,
                max_bytes_to_read=1024,
                timeout=3,
            )
            self.assertEqual(result.table_count, 1)
            self.assertEqual(result.column_count, 3)
            text = output.read_text(encoding="utf-8")
            self.assertNotIn("private_db", text)
            self.assertNotIn("private_table", text)
            self.assertNotIn("user_id", text)
            data = json.loads(text)
            self.assertEqual(data["tables"][0]["columns"][0]["role"], "id")
            self.assertEqual(data["summary"]["table_count"], 1)
            self.assertIn("column_profile_calibration", data["recommended_distribution_patch"])

    def test_http_error_triggers_kill_query(self):
        _FakeClickHouseHandler.fail_next = True
        client = ClickHouseHTTPClient(
            url=self.url,
            user=None,
            password=None,
            timeout=3,
            query_prefix="test_profile",
        )
        with self.assertRaises(Exception):
            client.execute_json("SELECT 1", settings={"max_execution_time": 1})
        self.assertEqual(_FakeClickHouseHandler.kill_count, 1)

    def test_profile_continues_after_table_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            _FakeClickHouseHandler.fail_next = False
            output = Path(tmp) / "profile.json"

            original_do_post = _FakeClickHouseHandler.do_POST

            def failing_value_query(handler):  # noqa: ANN001
                body = handler.rfile.read(int(handler.headers.get("Content-Length", "0"))).decode("utf-8")
                _FakeClickHouseHandler.requests.append(body)
                if body.startswith("KILL QUERY"):
                    _FakeClickHouseHandler.kill_count += 1
                    handler._json({"data": [], "rows": 0})
                    return
                if "FROM system.columns" in body:
                    handler._json(
                        {
                            "data": [
                                {"database": "private_db", "table": "private_table", "name": "user_id", "type": "UInt64"},
                            ],
                            "rows": 1,
                        }
                    )
                    return
                handler.send_response(500)
                handler.end_headers()
                handler.wfile.write(b"forced value query error")

            try:
                _FakeClickHouseHandler.do_POST = failing_value_query
                result = profile_columns(
                    http_url=self.url,
                    output_path=output,
                    tables=["private_db.private_table"],
                    max_execution_time=3,
                    max_bytes_to_read=1024,
                    timeout=3,
                )
            finally:
                _FakeClickHouseHandler.do_POST = original_do_post

            self.assertEqual(result.table_count, 0)
            self.assertEqual(result.killed_queries, 1)
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(data["errors"][0]["message"], "profile_query_failed_or_timed_out")
            self.assertNotIn("private_table", json.dumps(data))


if __name__ == "__main__":
    unittest.main()
