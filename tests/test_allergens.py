import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parents[1]))

from agent.adapters.base import AdapterError
from agent.allergens import (
    DEFAULT_SYNONYMS_PATH,
    contains_allergen,
    expand_allergens,
    filter_suggestions,
    load_allergens,
    load_synonyms,
    matched_profile_allergens,
    normalize,
)


def write_json(directory: str, name: str, payload) -> Path:
    path = Path(directory) / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class NormalizeTests(unittest.TestCase):
    def test_lowercase_and_yo_replaced(self):
        self.assertEqual(normalize("МЁД"), "мед")
        self.assertEqual(normalize("Сгущёнка"), "сгущенка")


class MatchingTests(unittest.TestCase):
    """Правила сравнения из докстроки agent/allergens.py."""

    def test_case_insensitive(self):
        self.assertTrue(contains_allergen("Салат с грецкими Орехами", {"орехи"}))
        self.assertFalse(contains_allergen("Куриное филе с брокколи", {"орехи", "мед"}))

    def test_yo_is_matched_in_both_text_and_allergen(self):
        """Регрессия: аллерген «мёд» не ловил «медом»/«медовик» (опасный пропуск)."""
        self.assertTrue(contains_allergen("Торт с медом", {"мёд"}))
        self.assertTrue(contains_allergen("Медовик со сгущёнкой", {"мед"}))
        self.assertTrue(contains_allergen("Торт с мёдом", {"мед"}))

    def test_stem_covers_word_forms(self):
        for text in ("Пирог с орехами", "ореховый соус", "ОРЕХИ"):
            self.assertTrue(contains_allergen(text, {"орехи"}), text)

    def test_short_allergen_does_not_match_everything(self):
        """Регрессия: основа «арах» совпадала с союзом «а» и скрывала всё подряд.

        Профиль с нормальными аллергенами не должен реагировать на служебные
        слова русского языка.
        """
        profile = {"арахис", "глютен", "мед", "орехи"}
        safe = [
            "Курица с рисом, а на гарнир овощи",
            "Суп и каша",
            "Рыба под соусом, к ней картофель",
            "Не острое рагу из овощей",
        ]
        for text in safe:
            self.assertFalse(contains_allergen(text, profile), text)

    def test_short_allergen_in_profile_matches_only_exactly(self):
        """Короткое слово профиля остаётся точным совпадением (защита от пропуска)."""
        self.assertTrue(contains_allergen("Икра кабачковая", {"икра"}))
        self.assertTrue(contains_allergen("Соус а-ля провансаль", {"а"}))
        self.assertFalse(contains_allergen("Кабачки тушёные", {"а"}))

    def test_multiword_allergen_hides_on_any_word(self):
        """ИЛИ-семантика многословного аллергена зафиксирована намеренно.

        «Коровье молоко» скрывает и «королевские креветки» — перестраховка:
        ложно скрытое блюдо неудобно, ложно показанное опасно.
        """
        allergens = {"коровье молоко"}
        self.assertTrue(contains_allergen("Каша на коровьем молоке", allergens))
        self.assertTrue(contains_allergen("Молочный коктейль", allergens))
        self.assertTrue(contains_allergen("Королевские креветки", allergens))
        self.assertFalse(contains_allergen("Говядина с рисом", allergens))


class SynonymTests(unittest.TestCase):
    SYNONYMS = {"глютен": {"пшеница", "мука"}, "молоко": {"сыр", "лактоза"}}

    def test_expand_adds_synonyms_and_keeps_the_term(self):
        self.assertEqual(
            expand_allergens({"Глютен"}, self.SYNONYMS),
            {"глютен", "пшеница", "мука"},
        )

    def test_expand_uses_words_of_multiword_allergen(self):
        expanded = expand_allergens({"коровье молоко"}, self.SYNONYMS)
        self.assertIn("сыр", expanded)
        self.assertIn("коровье молоко", expanded)

    def test_unknown_allergen_is_kept_as_is(self):
        self.assertEqual(expand_allergens({"сельдерей"}, self.SYNONYMS), {"сельдерей"})

    def test_semantic_gap_is_closed(self):
        """Без словаря «глютен» не ловит «пасту из пшеницы с мукой»."""
        self.assertFalse(contains_allergen("Паста из пшеницы с мукой", {"глютен"}))
        self.assertTrue(
            contains_allergen("Паста из пшеницы с мукой",
                              expand_allergens({"глютен"}, self.SYNONYMS))
        )

    def test_shipped_dictionary_is_valid_and_useful(self):
        table = load_synonyms()
        self.assertTrue(DEFAULT_SYNONYMS_PATH.exists(), "data/allergen_synonyms.json потерян")
        self.assertNotIn("_comment", table)  # служебные ключи не аллергены
        self.assertIn("пшеница", table["глютен"])
        self.assertIn("сыр", table["молоко"])
        self.assertTrue(
            contains_allergen("Паста из пшеницы с мукой", expand_allergens({"глютен"}, table))
        )

    def test_missing_dictionary_is_not_an_error(self):
        self.assertEqual(load_synonyms(Path("/nonexistent/synonyms.json")), {})

    def test_broken_dictionary_is_reported(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "synonyms.json"
            path.write_text("{ не json", encoding="utf-8")
            with self.assertRaisesRegex(AdapterError, "корректным JSON"):
                load_synonyms(path)

            write_json(directory, "list.json", ["глютен"])
            with self.assertRaisesRegex(AdapterError, "объектом"):
                load_synonyms(Path(directory) / "list.json")

            write_json(directory, "str.json", {"глютен": "пшеница"})
            with self.assertRaisesRegex(AdapterError, "списком слов"):
                load_synonyms(Path(directory) / "str.json")


class FilterTests(unittest.TestCase):
    def test_filter_drops_lines_with_allergens(self):
        text = (
            "1. Курица с брокколи — филе, брокколи, соль\n"
            "2. Салат с орехами и медом — листья, орехи, мед\n"
            "3. Суп из овощей — вода, картофель, морковь"
        )
        kept, dropped = filter_suggestions(text, {"орехи", "мед"})
        self.assertEqual(dropped, 1)
        self.assertIn("Курица с брокколи", kept)
        self.assertIn("Суп из овощей", kept)
        self.assertNotIn("Салат с орехами", kept)

    def test_filter_keeps_everything_without_allergens(self):
        kept, dropped = filter_suggestions("1. Омлет — яйцо, молоко", set())
        self.assertEqual(dropped, 0)
        self.assertIn("Омлет", kept)

    def test_filter_all_lines_dropped(self):
        kept, dropped = filter_suggestions("1. Торт с орехами", {"орехи"})
        self.assertEqual(dropped, 1)
        self.assertEqual(kept, "")

    def test_filter_accepts_iterators(self):
        kept, dropped = filter_suggestions("1. Торт с орехами", (a for a in ["орехи"]))
        self.assertEqual((kept, dropped), ("", 1))

    def test_filter_uses_synonyms_of_shipped_dictionary(self):
        kept, dropped = filter_suggestions(
            "1. Паста из пшеницы с мукой\n2. Рис с овощами",
            expand_allergens({"глютен"}),
        )
        self.assertEqual(dropped, 1)
        self.assertEqual(kept, "2. Рис с овощами")

    def test_matched_profile_allergens_reports_profile_words(self):
        """В отчёте должны быть слова профиля, а не внутренние синонимы."""
        self.assertEqual(
            matched_profile_allergens("Салат с фундуком и медом", {"орехи", "мед"}),
            ["мед", "орехи"],
        )
        self.assertEqual(
            matched_profile_allergens("Салат с фундуком", {"орехи", "мед"}),
            ["орехи"],
        )
        self.assertEqual(matched_profile_allergens("Рис с овощами", {"орехи"}), [])


class ProfileLoadingTests(unittest.TestCase):
    def test_load_allergens_normalizes(self):
        with TemporaryDirectory() as directory:
            path = write_json(directory, "profile.json",
                              {"family_id": "x", "allergens": [" Орехи ", "МЁД", ""]})
            self.assertEqual(load_allergens(path), {"орехи", "мед"})

    def test_load_allergens_without_field_is_empty_not_error(self):
        with TemporaryDirectory() as directory:
            path = write_json(directory, "profile.json", {"family_id": "x"})
            self.assertEqual(load_allergens(path), set())

    def test_load_allergens_missing_file(self):
        with self.assertRaisesRegex(AdapterError, "не найден"):
            load_allergens(Path("/nonexistent/profile.json"))

    def test_load_allergens_bad_structure(self):
        with TemporaryDirectory() as directory:
            path = write_json(directory, "profile.json", {"allergens": "орехи"})
            with self.assertRaisesRegex(AdapterError, "списком"):
                load_allergens(path)

            write_json(directory, "list.json", ["орехи"])
            with self.assertRaisesRegex(AdapterError, "объектом"):
                load_allergens(Path(directory) / "list.json")

            broken = Path(directory) / "broken.json"
            broken.write_text("{ не json", encoding="utf-8")
            with self.assertRaisesRegex(AdapterError, "корректным JSON"):
                load_allergens(broken)

    def test_demo_profile_of_repository_is_loadable(self):
        allergens = load_allergens(Path(__file__).parents[1] / "data" / "family_profile.json")
        self.assertTrue(allergens)


if __name__ == "__main__":
    unittest.main()
