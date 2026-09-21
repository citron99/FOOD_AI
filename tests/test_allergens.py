import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parents[1]))

from agent.adapters.base import AdapterError
from agent.allergens import contains_allergen, filter_suggestions, load_allergens


class AllergenFilterTests(unittest.TestCase):
    def test_contains_allergen_case_insensitive(self):
        allergens = {"орехи", "мёд"}
        self.assertTrue(contains_allergen("Салат с грецкими Орехами", allergens))
        self.assertTrue(contains_allergen("Торт с мёдом", allergens))
        self.assertFalse(contains_allergen("Куриное филе с брокколи", allergens))

    def test_filter_drops_lines_with_allergens(self):
        text = (
            "1. Курица с брокколи — филе, брокколи, соль\n"
            "2. Салат с орехами и мёдом — листья, орехи, мёд\n"
            "3. Суп из овощей — вода, картофель, морковь"
        )
        kept, dropped = filter_suggestions(text, {"орехи", "мёд"})
        self.assertEqual(dropped, 1)
        self.assertIn("Курица с брокколи", kept)
        self.assertIn("Суп из овощей", kept)
        self.assertNotIn("Салат с орехами", kept)

    def test_filter_keeps_everything_without_allergens(self):
        text = "1. Омлет — яйцо, молоко"
        kept, dropped = filter_suggestions(text, set())
        self.assertEqual(dropped, 0)
        self.assertIn("Омлет", kept)

    def test_filter_all_lines_dropped(self):
        kept, dropped = filter_suggestions("1. Торт с орехами", {"орехи"})
        self.assertEqual(dropped, 1)
        self.assertEqual(kept, "")

    def test_load_allergens_normalizes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps({"family_id": "x", "allergens": [" Орехи ", "МЁД", ""]}),
                            encoding="utf-8")
            self.assertEqual(load_allergens(path), {"орехи", "мёд"})

    def test_load_allergens_missing_file(self):
        with self.assertRaisesRegex(AdapterError, "не найден"):
            load_allergens(Path("/nonexistent/profile.json"))

    def test_load_allergens_bad_structure(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps({"allergens": "орехи"}), encoding="utf-8")
            with self.assertRaisesRegex(AdapterError, "списком"):
                load_allergens(path)


if __name__ == "__main__":
    unittest.main()
