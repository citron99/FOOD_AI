import json
import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parents[1]))

from agent.cli import analyze, load_products


class CliAgentTests(unittest.TestCase):
    def test_expired_and_urgent_are_separated(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps({"products": [
                {"name": "A", "quantity": 1, "unit": "шт.", "location": "fridge", "expires_at": "2026-08-17"},
                {"name": "B", "quantity": 1, "unit": "шт.", "location": "fridge", "expires_at": "2026-08-20"},
                {"name": "C", "quantity": 1, "unit": "шт.", "location": "pantry", "expires_at": "2026-09-01"}
            ]}), encoding="utf-8")
            result = analyze(load_products(path), date(2026, 8, 18), 3)
            self.assertEqual(result["summary"]["expired"], 1)
            self.assertEqual(result["summary"]["urgent"], 1)
            self.assertEqual(result["groups"]["urgent"][0]["name"], "B")

    def test_negative_quantity_is_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps({"products": [{"name": "bad", "quantity": -1}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Отрицательный остаток"):
                load_products(path)

    def test_missing_file_is_reported_clearly(self):
        with self.assertRaisesRegex(ValueError, "не найден"):
            load_products(Path("/nonexistent/inventory.json"))

    def test_invalid_json_is_reported_clearly(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text("{ not json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON"):
                load_products(path)

    def test_invalid_date_format_is_reported_clearly(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps({"products": [
                {"name": "Молоко", "quantity": 1, "expires_at": "18.08.2026"}
            ]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Неверный формат даты"):
                load_products(path)

    def test_missing_required_field_is_reported_clearly(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps({"products": [{"name": "Без остатка"}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "quantity"):
                load_products(path)


if __name__ == "__main__":
    unittest.main()
