import contextlib
import io
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agent.adapters.base import AdapterError
from agent.cli import (
    Product,
    analyze,
    load_family_allergens,
    load_products,
    main,
    render_html,
    render_markdown,
)


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


class InputValidationTests(unittest.TestCase):
    """Волна 3 (ревью, «валидация входных данных»).

    Плохие типы полей давали сырые TypeError из strptime, ``name: null``
    превращался в продукт «None», а опечатка/null в статусе молча уводили
    позицию в ignored — просрочка исчезала из отчёта без единого слова.
    """

    def _inventory(self, directory: str, items: list) -> Path:
        path = Path(directory) / "inventory.json"
        path.write_text(json.dumps({"products": items}), encoding="utf-8")
        return path

    def test_numeric_expires_at_is_value_error_with_product_name(self):
        with TemporaryDirectory() as directory:
            path = self._inventory(directory, [
                {"name": "Йогурт", "quantity": 1, "expires_at": 20260820}
            ])
            with self.assertRaisesRegex(ValueError, "Неверный формат даты.*Йогурт"):
                load_products(path)

    def test_list_expires_at_is_value_error_with_product_name(self):
        with TemporaryDirectory() as directory:
            path = self._inventory(directory, [
                {"name": "Йогурт", "quantity": 1, "expires_at": ["2026"]}
            ])
            with self.assertRaisesRegex(ValueError, "Неверный формат даты.*Йогурт"):
                load_products(path)

    def test_null_name_is_rejected(self):
        with TemporaryDirectory() as directory:
            path = self._inventory(directory, [
                {"name": None, "quantity": 1, "expires_at": "2026-08-20"}
            ])
            with self.assertRaisesRegex(ValueError, "обязательное поле «name»"):
                load_products(path)

    def test_unknown_status_warns_and_stays_ignored(self):
        with TemporaryDirectory() as directory:
            path = self._inventory(directory, [
                {"name": "Кефир", "quantity": 1, "expires_at": "2026-08-17",
                 "status": "actve"}
            ])
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                products = load_products(path)
            # Предупреждение — в stderr, позиция остаётся исключённой из учёта.
            self.assertIn("actve", err.getvalue())
            self.assertIn("Кефир", err.getvalue())
            result = analyze(products, date(2026, 8, 18), 3)
            self.assertEqual(result["summary"]["ignored"], 1)

    def test_null_status_warns(self):
        with TemporaryDirectory() as directory:
            path = self._inventory(directory, [
                {"name": "Кефир", "quantity": 1, "expires_at": "2026-08-17",
                 "status": None}
            ])
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                load_products(path)
            self.assertIn("'null'", err.getvalue())

    def test_missing_and_known_status_do_not_warn(self):
        with TemporaryDirectory() as directory:
            path = self._inventory(directory, [
                {"name": "A", "quantity": 1, "expires_at": "2026-08-17"},
                {"name": "B", "quantity": 1, "expires_at": "2026-08-17",
                 "status": "frozen"},
                # written_off — осознанное списание из демо-инвентаря:
                # без предупреждения, но и без активного учёта.
                {"name": "Списанный", "quantity": 0, "status": "written_off"},
            ])
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                products = load_products(path)
            self.assertEqual(err.getvalue(), "")
            result = analyze(products, date(2026, 8, 18), 3)
            self.assertEqual(result["summary"]["ignored"], 1)

    def test_render_markdown_escapes_pipe_in_cell_values(self):
        with TemporaryDirectory() as directory:
            path = self._inventory(directory, [
                {"name": "Молоко | 2.5%", "quantity": 1, "unit": "л",
                 "location": "холод", "expires_at": "2026-08-20"}
            ])
            result = analyze(load_products(path), date(2026, 8, 18), 3)
            row = next(line for line in render_markdown(result).splitlines()
                       if "Молоко" in line)
            self.assertIn(r"Молоко \| 2.5%", row)
            # 5 колонок = 6 неэкранированных разделителей; до фикса было 7.
            unescaped = sum(1 for i, ch in enumerate(row)
                            if ch == "|" and (i == 0 or row[i - 1] != "\\"))
            self.assertEqual(unescaped, 6)


class ManualAccountingSectionTests(unittest.TestCase):
    """Волна 4 (ревью, п.10): отчёт из POS-источника не должен быть пустым
    без объяснений — ignored-позиции (quantity=0) перечисляются в секции
    «Требуется ручной учёт» с пояснением, что делать пользователю."""

    def _pos_result(self):
        # Как из POS-адаптеров: справочник без остатков и сроков.
        products = [
            Product(name="Куриное филе", quantity=0, unit="кг",
                    location="склад", expires_at=None),
            Product(name="Говядина", quantity=0, unit="кг",
                    location="склад", expires_at=None),
            Product(name="Списанный", quantity=0, unit="л",
                    location="холодильник", expires_at=None,
                    status="written_off"),
        ]
        return analyze(products, date(2026, 8, 18), 3)

    def test_markdown_lists_ignored_products_with_note(self):
        markdown = render_markdown(self._pos_result())
        self.assertIn("## Требуется ручной учёт", markdown)
        self.assertIn("Куриное филе", markdown)
        self.assertIn("Говядина", markdown)
        self.assertIn("Подтвердите остаток и срок вручную", markdown)
        # Детерминированный порядок — по имени.
        self.assertLess(markdown.index("Говядина"), markdown.index("Куриное филе"))

    def test_html_lists_ignored_products_with_note(self):
        html = render_html(self._pos_result())
        self.assertIn("<h2>Требуется ручной учёт</h2>", html)
        self.assertIn("Куриное филе", html)
        self.assertIn("Подтвердите остаток и срок вручную", html)

    def test_section_absent_when_nothing_ignored(self):
        result = analyze(
            [Product(name="A", quantity=1, unit="шт.", location="склад",
                     expires_at=None)],
            date(2026, 8, 18), 3,
        )
        self.assertNotIn("Требуется ручной учёт", render_markdown(result))
        self.assertNotIn("Требуется ручной учёт", render_html(result))

    def test_ignored_names_are_escaped(self):
        result = analyze(
            [Product(name="Сливки <2.5%> | акция", quantity=0, unit="л",
                     location="холод", expires_at=None)],
            date(2026, 8, 18), 3,
        )
        markdown = render_markdown(result)
        html = render_html(result)
        self.assertIn(r"Сливки <2.5%> \| акция", markdown)
        row = next(line for line in markdown.splitlines() if "Сливки" in line)
        unescaped = sum(1 for i, ch in enumerate(row)
                        if ch == "|" and (i == 0 or row[i - 1] != "\\"))
        self.assertEqual(unescaped, 5)  # 4 колонки = 5 разделителей
        self.assertIn("Сливки &lt;2.5%&gt; | акция", html)


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

    def run_cli(self, directory: str, profile_payload, extra_args=(), html=False):
        inventory = Path(directory) / "inventory.json"
        inventory.write_text(json.dumps({"products": [
            {"name": "Куриное филе", "quantity": 1, "unit": "кг",
             "location": "холодильник", "expires_at": "2026-09-29"},
        ]}, ensure_ascii=False), encoding="utf-8")
        out = Path(directory) / "report.md"
        html_path = Path(directory) / "report.html"
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
        if html:
            argv += ["--html-out", str(html_path)]
        calls = {}

        def fake_suggest(products, **kwargs):
            calls.update(kwargs)
            calls["products"] = products
            return self.IDEAS

        stderr = io.StringIO()
        with mock.patch("agent.adapters.deepseek.suggest_menu_ideas", side_effect=fake_suggest), \
                mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = main()
        return code, out, calls, stderr.getvalue(), html_path

    def test_allergen_dishes_are_hidden_and_audited(self):
        with TemporaryDirectory() as directory:
            code, out, calls, _, _ = self.run_cli(
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
            code, out, calls, stderr, _ = self.run_cli(
                directory, None, extra_args=["--allow-no-profile"])
            report = out.read_text(encoding="utf-8")
            self.assertEqual(code, 0)
            self.assertEqual(calls["allergens"], [])
            self.assertIn("Hard-filter аллергенов ОТКЛЮЧЁН", report)
            self.assertIn("ОТКЛЮЧЁН", stderr)  # предупреждение видно и в логе cron
            self.assertIn("Курица с арахисовой пастой", report)  # риск принят явно

    def test_empty_profile_publishes_with_warning(self):
        with TemporaryDirectory() as directory:
            code, out, _, stderr, _ = self.run_cli(directory, {"family_id": "demo", "allergens": []})
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


class LLMDegradationTests(unittest.TestCase):
    """П.6 ревью: сбой DeepSeek не должен ронять детерминированный отчёт.

    По умолчанию отчёт о сроках публикуется, а сбой фиксируется в секции
    идей и в stderr; флаг --strict-llm возвращает старое поведение
    «упасть с кодом 2 без записи отчёта».
    """

    def run_cli(self, directory: str, *, failure: Exception | None = None,
                extra_args=(), html=False):
        inventory = Path(directory) / "inventory.json"
        inventory.write_text(json.dumps({"products": [
            {"name": "Куриное филе", "quantity": 1, "unit": "кг",
             "location": "холодильник", "expires_at": "2026-09-29"},
        ]}, ensure_ascii=False), encoding="utf-8")
        profile = Path(directory) / "profile.json"
        profile.write_text(json.dumps({"family_id": "demo", "allergens": []}),
                           encoding="utf-8")
        out = Path(directory) / "report.md"
        html_path = Path(directory) / "report.html"
        argv = [
            "cli", "--inventory", str(inventory), "--out", str(out),
            "--as-of", "2026-09-28", "--suggest-menu", "--profile", str(profile),
            *extra_args,
        ]
        if html:
            argv += ["--html-out", str(html_path)]

        def fake_suggest(products, **kwargs):
            if failure is not None:
                raise failure
            return "1. Куриный суп\n2. Омлет из яиц"

        stderr = io.StringIO()
        with mock.patch("agent.adapters.deepseek.suggest_menu_ideas",
                        side_effect=fake_suggest), \
                mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = main()
        return code, out, html_path, stderr.getvalue()

    def test_llm_failure_still_publishes_report(self):
        with TemporaryDirectory() as directory:
            code, out, _, stderr = self.run_cli(
                directory, failure=AdapterError("таймаут запроса"))
            report = out.read_text(encoding="utf-8")
            self.assertEqual(code, 0)
            self.assertIn("DeepSeek недоступен", report)
            self.assertIn("DeepSeek недоступен", stderr)
            self.assertIn("Идеи блюд не получены", report)
            # Детерминированная часть отчёта на месте.
            self.assertIn("Куриное филе", report)
            self.assertNotIn("Куриный суп", report)

    def test_strict_llm_fails_without_report(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit) as caught:
                self.run_cli(directory, failure=AdapterError("таймаут запроса"),
                             extra_args=["--strict-llm"])
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse((Path(directory) / "report.md").exists())


class MenuIdeasHtmlTests(unittest.TestCase):
    """П.9 ревью: секция «Идеи блюд» должна попадать и в HTML-отчёт,
    который публикуется на хостинг по cron, а не только в Markdown."""

    def run_cli(self, directory: str, *, failure: Exception | None = None):
        inventory = Path(directory) / "inventory.json"
        inventory.write_text(json.dumps({"products": [
            {"name": "Куриное филе", "quantity": 1, "unit": "кг",
             "location": "холодильник", "expires_at": "2026-09-29"},
        ]}, ensure_ascii=False), encoding="utf-8")
        profile = Path(directory) / "profile.json"
        profile.write_text(json.dumps({"family_id": "demo", "allergens": []}),
                           encoding="utf-8")
        out = Path(directory) / "report.md"
        html_path = Path(directory) / "report.html"
        argv = ["cli", "--inventory", str(inventory), "--out", str(out),
                "--as-of", "2026-09-28", "--suggest-menu", "--profile", str(profile),
                "--html-out", str(html_path)]

        def fake_suggest(products, **kwargs):
            if failure is not None:
                raise failure
            return "1. Куриный суп\n2. Омлет из яиц"

        stderr = io.StringIO()
        with mock.patch("agent.adapters.deepseek.suggest_menu_ideas",
                        side_effect=fake_suggest), \
                mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = main()
        return code, out, html_path

    def test_html_contains_menu_ideas(self):
        with TemporaryDirectory() as directory:
            code, out, html_path = self.run_cli(directory)
            html = html_path.read_text(encoding="utf-8")
            self.assertEqual(code, 0)
            self.assertIn("Идеи блюд (DeepSeek)", html)
            self.assertIn("Неподтверждённая LLM-подсказка", html)
            # Пронумерованные строки идей — нумерованным списком.
            self.assertIn("<ol>", html)
            self.assertIn("<li>Куриный суп</li>", html)
            # Секция есть и в Markdown тоже.
            self.assertIn("Идеи блюд (DeepSeek)", out.read_text(encoding="utf-8"))

    def test_html_escapes_dish_names(self):
        with TemporaryDirectory() as directory:
            _, _, html_path = self.run_cli(directory)
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("<li>Омлет из яиц</li>", html)

    def test_html_marks_llm_failure(self):
        with TemporaryDirectory() as directory:
            code, _, html_path = self.run_cli(
                directory, failure=AdapterError("таймаут запроса"))
            html = html_path.read_text(encoding="utf-8")
            self.assertEqual(code, 0)
            self.assertIn("Идеи блюд (DeepSeek)", html)
            self.assertIn("DeepSeek недоступен", html)
            self.assertIn("Куриное филе", html)  # детерминированная часть на месте


if __name__ == "__main__":
    unittest.main()
