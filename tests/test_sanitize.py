import unittest

from megabench.sanitize import sanitize_sql


class SanitizeSQLTest(unittest.TestCase):
    def test_masks_identifiers_and_literals(self):
        sql = """
        SELECT user_id, sum(cost)
        FROM real_db.real_table AS rt
        WHERE dt = '2026-07-01' AND campaign = 'secret' AND item_id = 123
        GROUP BY user_id
        """
        result = sanitize_sql(sql)

        self.assertNotIn("real_db", result.sql)
        self.assertNotIn("real_table", result.sql)
        self.assertNotIn("secret", result.sql)
        self.assertNotIn("user_id", result.sql)
        self.assertIn("events_wide_001", result.sql)
        self.assertIn("{{date}}", result.sql)
        self.assertIn("{{str}}", result.sql)
        self.assertIn("{{int}}", result.sql)

    def test_template_key_ignores_literal_values(self):
        left = sanitize_sql("SELECT count() FROM a.b WHERE dt = '2026-07-01' AND x = 1")
        right = sanitize_sql("SELECT count() FROM a.b WHERE dt = '2026-07-02' AND x = 2")
        self.assertEqual(left.template_key, right.template_key)


if __name__ == "__main__":
    unittest.main()
