import contextlib
import io
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))

from agent.cli import analyze, load_family_allergens, load_products, main, render_html


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

    def test_render_html_produces_standalone_page(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps({"products": [
                {"name": "Молоко <2.5%>", "quantity": 1, "unit": "л",
                 "location": "холодильник", "expires_at": "2026-08-17"}
            ]}), encoding="utf-8")
            result = analyze(load_products(path), date(2026, 8, 18), 3)
            html = render_html(result)
            self.assertIn("<!DOCTYPE html>", html)
            self.assertIn("Молоко &lt;2.5%&gt;", html)  # HTML-экранирование
            self.assertIn("Сводка", html)
            self.assertIn("</html>", html)
            self.assertNotIn("<script", html)


class AllergenProfileTests(unittest.TestCase):
    """Профиль семьи — часть защитного слоя, его отсутствие не должно быть тихим."""

    def test_existing_profile_is_loaded(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps({"allergens": ["орехи"]}), encoding="utf-8")
            allergens, warnings = load_family_allergens(path)
            self.assertEqual(allergens, {"орехи"})
            self.assertEqual(warnings, [])

    def test_missing_profile_without_flag_is_an_error(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "absent.json"
            with self.assertRaisesRegex(ValueError, "--allow-no-profile"):
                load_family_allergens(path)

    def test_missing_profile_with_flag_warns_loudly(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "absent.json"
            allergens, warnings = load_family_allergens(path, allow_no_profile=True)
            self.assertEqual(allergens, set())
            self.assertEqual(len(warnings), 1)
            self.assertIn("ОТКЛЮЧЁН", warnings[0])

    def test_empty_profile_warns_instead_of_filtering_nothing(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps({"allergens": []}), encoding="utf-8")
            allergens, warnings = load_family_allergens(path)
            self.assertEqual(allergens, set())
            self.assertIn("не содержит ни одного аллергена", warnings[0])


class SuggestMenuReportTests(unittest.TestCase):
    """Сквозная проверка секции «Идеи блюд» с подменённым DeepSeek."""

    IDEAS = (
        "1. Курица с арахисовой пастой — филе, арахис\n"
        "2. Куриный суп — филе, вода, морковь\n"
        "3. Паста из пшеницы с мукой — тесто, сыр"
    )

    def run_cli(self, directory: str, profile_payload, extra_args=()):
        inventory = Path(directory) / "inventory.json"
        inventory.write_text(json.dumps({"products": [
            {"name": "Куриное филе", "quantity": 1, "unit": "кг",
             "location": "холодильник", "expires_at": "2026-09-29"},
        ]}), encoding="utf-8")
        out = Path(directory) / "report.md"
        if profile_payload is not None:
            profile = Path(directory) / "profile.json"
            profile.write_text(json.dumps(profile_payload, ensure_ascii=False), encoding="utf-8")
        else:
            profile = Path(directory) / "absent.json"
        argv = [
            "cli", "--inventory", str(inventory), "--out", str(out),
            "--as-of", "2026-09-28", "--suggest-menu", "--profile", str(profile),
            *extra_args,
        ]
        calls = {}

        def fake_suggest(products, **kwargs):
            calls.update(kwargs)
            calls["products"] = products
            return self.IDEAS

        stderr = io.StringIO()
        with mock.patch("agent.adapters.deepseek.suggest_menu_ideas", side_effect=fake_suggest):
            with mock.patch.object(sys, "argv", argv):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
                    code = main()
        return code, out, calls, stderr.getvalue()

    def test_allergen_dishes_are_hidden_and_audited(self):
        with TemporaryDirectory() as directory:
            code, out, calls, _ = self.run_cli(
                directory, {"family_id": "demo", "allergens": ["арахис", "глютен"]})
            report = out.read_text(encoding="utf-8")
            self.assertEqual(code, 0)
            self.assertEqual(calls["allergens"], ["арахис", "глютен"])  # уехали в промпт
            self.assertIn("Куриный суп", report)
            self.assertNotIn("арахисовой пастой", report)   # скрыто hard-фильтром
            self.assertNotIn("Паста из пшеницы", report)    # скрыто через синонимы глютена
            self.assertIn("Скрыто блюд с аллергенами профиля: **2** (арахис, глютен)", report)

    def test_missing_profile_stops_the_run(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit) as caught:
                self.run_cli(directory, None)
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse((Path(directory) / "report.md").exists())

    def test_allow_no_profile_publishes_with_warning(self):
        with TemporaryDirectory() as directory:
            code, out, calls, stderr = self.run_cli(
                directory, None, extra_args=["--allow-no-profile"])
            report = out.read_text(encoding="utf-8")
            self.assertEqual(code, 0)
            self.assertEqual(calls["allergens"], [])
            self.assertIn("Hard-filter аллергенов ОТКЛЮЧЁН", report)
            self.assertIn("ОТКЛЮЧЁН", stderr)  # предупреждение видно и в логе cron
            self.assertIn("Курица с арахисовой пастой", report)  # риск принят явно

    def test_empty_profile_publishes_with_warning(self):
        with TemporaryDirectory() as directory:
            code, out, _, stderr = self.run_cli(directory, {"family_id": "demo", "allergens": []})
            report = out.read_text(encoding="utf-8")
            self.assertEqual(code, 0)
            self.assertIn("не содержит ни одного аллергена", report)
            self.assertIn("не содержит ни одного аллергена", stderr)
            self.assertIn("Курица с арахисовой пастой", report)

    def test_suggest_menu_is_not_required(self):
        """Без --suggest-menu профиль не проверяется: детерминированный отчёт независим от LLM."""
        with TemporaryDirectory() as directory:
            inventory = Path(directory) / "inventory.json"
            inventory.write_text(json.dumps({"products": [
                {"name": "Рис", "quantity": 1, "unit": "кг",
                 "location": "шкаф", "expires_at": "2026-09-29"},
            ]}), encoding="utf-8")
            out = Path(directory) / "report.md"
            argv = ["cli", "--inventory", str(inventory), "--out", str(out),
                    "--as-of", "2026-09-28", "--profile", str(Path(directory) / "absent.json")]
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            self.assertNotIn("Идеи блюд", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
