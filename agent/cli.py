"""SmartKitchen Family CLI agent.

The first MVP task is intentionally deterministic: identify products that are
expired or approaching expiry and produce a safe, auditable report. No LLM is
required, so the agent works without external keys.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

# Реестр источников импортируется на уровне модуля: волна 5 (ревью, п.13)
# перенесла Product/parse_date в agent/models.py, поэтому цикла импортов
# «cli ← registry ← adapters ← cli» больше нет — слои направленные:
# models ← adapters ← cli.
from agent.adapters.registry import available_sources, load_source
from agent.models import Product, parse_date


def load_products(path: Path) -> list[Product]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"Файл инвентаря не найден: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Файл инвентаря не является корректным JSON: {path} ({exc})") from None
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict):
        raw_items = payload.get("products", [])
    else:
        raise ValueError(f"Неверная структура инвентаря в {path}: ожидается объект с полем «products» или список")  # noqa: TRY004 — волна 3: валидация входа даёт ValueError, а не TypeError
    if not isinstance(raw_items, list):
        raise ValueError(f"Поле «products» в {path} должно быть списком")  # noqa: TRY004
    products: list[Product] = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError(f"Каждая позиция инвентаря должна быть объектом: {item!r}")  # noqa: TRY004
        if "name" not in item or item["name"] is None:
            raise ValueError("В позиции инвентаря отсутствует обязательное поле «name»")
        name = str(item["name"])
        if "quantity" not in item:
            raise ValueError(f"В позиции инвентаря отсутствует обязательное поле «quantity»: {name}")
        try:
            quantity = float(item["quantity"])
        except (TypeError, ValueError):
            raise ValueError(f"Нечисловой остаток у продукта «{name}»: {item['quantity']!r}") from None
        if quantity < 0:
            raise ValueError(f"Отрицательный остаток запрещён: {name}")
        # Волна 3 (ревью, «валидация входных данных»): опечатка («actve») или
        # null в статусе раньше молча уводили позицию в «Вне активного учёта»,
        # и просрочка исчезала из отчёта без единого слова. Позиция остаётся
        # исключённой (за пользователя не гадаем), но предупреждаем в stderr.
        raw_status = item.get("status", "active")
        status = str(raw_status) if raw_status is not None else "null"
        if status not in KNOWN_STATUSES:
            print(
                f"⚠️ Позиция «{name}» имеет неизвестный статус {status!r} "
                "и исключена из активного учёта. Задайте «active» или «frozen», "
                "если позиция должна попадать в отчёт.",
                file=sys.stderr,
            )
        products.append(Product(
            name=name,
            quantity=quantity,
            unit=str(item.get("unit", "шт.")),
            location=str(item.get("location", "не указано")),
            expires_at=parse_date(item.get("expires_at"), name),
            status=status,
        ))
    return products


# Статусы, за которыми classify ведёт активный учёт (сроки, отчёт).
TRACKED_STATUSES = frozenset({"active", "frozen"})
# Осознанное исключение из учёта (списано) — не опечатка, предупреждать не нужно.
UNTRACKED_STATUSES = frozenset({"written_off"})
KNOWN_STATUSES = TRACKED_STATUSES | UNTRACKED_STATUSES


def classify(product: Product, today: date, warning_days: int) -> str:
    if product.status not in TRACKED_STATUSES or product.quantity <= 0:
        return "ignore"
    if product.expires_at is None:
        return "no_date"
    days_left = (product.expires_at - today).days
    if days_left < 0:
        return "expired"
    if days_left <= warning_days:
        return "urgent"
    return "normal"


def analyze(products: list[Product], today: date, warning_days: int) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {"expired": [], "urgent": [], "normal": [], "no_date": [], "ignored": []}
    for product in products:
        bucket = classify(product, today, warning_days)
        if bucket == "ignore":
            bucket = "ignored"
        days_left = None if product.expires_at is None else (product.expires_at - today).days
        groups[bucket].append({
            "name": product.name,
            "quantity": product.quantity,
            "unit": product.unit,
            "location": product.location,
            "expires_at": product.expires_at.isoformat() if product.expires_at else None,
            "days_left": days_left,
            "status": product.status,
        })
    groups["expired"].sort(key=lambda x: x["days_left"])
    groups["urgent"].sort(key=lambda x: x["days_left"])
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "as_of": today.isoformat(),
        "warning_days": warning_days,
        "summary": {key: len(value) for key, value in groups.items()},
        "groups": groups,
    }


def _md_cell(value: Any) -> str:
    """Экранирование значения ячейки Markdown-таблицы (п. «валидация входных
    данных» ревью): сырое «|» в названии добавляло колонку и ломало строку."""
    return (str(value).replace("\\", "\\\\").replace("|", "\\|")
            .replace("\r", " ").replace("\n", " "))


def render_markdown(result: dict[str, Any]) -> str:
    s = result["summary"]
    lines = [
        "# SmartKitchen Family — отчёт о срочных продуктах",
        "",
        f"> Дата расчёта: **{result['as_of']}**. Порог предупреждения: **{result['warning_days']} дн.**",
        "",
        "## Сводка",
        "",
        "| Категория | Количество |",
        "|---|---:|",
        f"| Просрочены | {s['expired']} |",
        f"| Использовать в ближайшие дни | {s['urgent']} |",
        f"| Без даты срока | {s['no_date']} |",
        f"| Вне активного учёта | {s['ignored']} |",
        "",
    ]
    for title, key, note in [
        ("Просроченные продукты", "expired", "Не использовать без проверки безопасности и решения пользователя."),
        ("Продукты с приближающимся сроком", "urgent", "Рекомендуется включить в ближайшее меню."),
        ("Активные продукты без даты срока", "no_date", "Нужно уточнить срок вручную, если он критичен для безопасности."),
    ]:
        lines += [f"## {title}", "", f"> {note}", ""]
        items = result["groups"][key]
        if not items:
            lines += ["Нет позиций.", ""]
            continue
        lines += ["| Продукт | Остаток | Место | Срок | Осталось дней |", "|---|---:|---|---|---:|"]
        for item in items:
            days = "—" if item["days_left"] is None else str(item["days_left"])
            expiry = item["expires_at"] or "—"
            lines.append(
                f"| {_md_cell(item['name'])} | {_md_cell(item['quantity'])} {_md_cell(item['unit'])}"
                f" | {_md_cell(item['location'])} | {_md_cell(expiry)} | {_md_cell(days)} |"
            )
        lines.append("")
    ignored_items = sorted(result["groups"]["ignored"], key=lambda x: x["name"])
    if ignored_items:
        # П.10 ревью: POS-импорт даёт quantity=0, и отчёт выглядел пустым,
        # не показывая, что именно ждёт ручного подтверждения остатков.
        lines += [
            "## Требуется ручной учёт",
            "",
            (
                "> Позиции вне активного учёта: остаток нулевой (POS-справочники "
                "импортируются без остатков и сроков) или статус исключает позицию. "
                "Подтвердите остаток и срок вручную, чтобы позиция попала в расчёт."
            ),
            "",
            "| Продукт | Остаток | Место | Статус |",
            "|---|---:|---|---|",
        ]
        for item in ignored_items:
            lines.append(
                f"| {_md_cell(item['name'])} | {_md_cell(item['quantity'])} {_md_cell(item['unit'])}"
                f" | {_md_cell(item['location'])} | {_md_cell(item['status'])} |"
            )
        lines.append("")
    return "\n".join(lines)


_PAGE_CSS = """
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;max-width:900px;
margin:0 auto;padding:24px;color:#1a1a2e;background:#fafafa}
h1{font-size:1.6rem} h2{margin-top:2rem;border-bottom:2px solid #e0e0e0;padding-bottom:4px}
table{border-collapse:collapse;width:100%;margin:1rem 0;background:#fff}
th,td{border:1px solid #ddd;padding:8px 12px;text-align:left}
th{background:#f0f4f8} tr:nth-child(even){background:#f9f9f9}
blockquote{background:#fff8e6;border-left:4px solid #f0b400;margin:1rem 0;
padding:10px 16px;border-radius:4px}
footer{margin-top:3rem;font-size:.85rem;color:#888;border-top:1px solid #e0e0e0;padding-top:12px}
"""


def _esc(value: Any) -> str:
    """Экранирование HTML-символов для безопасной вставки текста."""
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_IDEA_LINE = re.compile(r"^(\d+)[.)]\s+(.*)$")


def _ideas_to_html(ideas: str) -> str:
    """Разметка тела идей: пронумерованные строки — нумерованным списком, остальное — абзацами."""
    out: list[str] = []
    items: list[str] = []
    for line in ideas.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        match = _IDEA_LINE.match(line)
        if match:
            items.append(match.group(2))
            continue
        if items:
            out.append("<ol>" + "".join(f"<li>{_esc(i)}</li>" for i in items) + "</ol>")
            items = []
        out.append(f"<p>{_esc(line)}</p>")
    if items:
        out.append("<ol>" + "".join(f"<li>{_esc(i)}</li>" for i in items) + "</ol>")
    return "\n".join(out)


def render_html(result: dict[str, Any],
                menu_ideas: tuple[list[str], str] | None = None) -> str:
    """Автономная HTML-страница отчёта без внешних зависимостей.

    Нужна для публикации отчёта на хостинге (например, по cron на Beget),
    где нет библиотек конвертации Markdown. Секция идей блюд (п.9 ревью)
    раньше дописывалась только в Markdown после рендера — на cron публиковался
    HTML без неё; теперь оба рендера получают одни и те же ``menu_ideas``.
    """
    s = result["summary"]
    parts = [
        "<!DOCTYPE html>", '<html lang="ru">', "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>SmartKitchen Family — отчёт о продуктах</title>",
        f"<style>{_PAGE_CSS}</style>", "</head>", "<body>",
        "<h1>SmartKitchen Family — отчёт о срочных продуктах</h1>",
        "<blockquote>Дата расчёта: <b>", _esc(result["as_of"]), "</b>. Порог предупреждения: <b>",
        _esc(result["warning_days"]), " дн.</b></blockquote>",
        "<h2>Сводка</h2>", "<table><tr><th>Категория</th><th>Количество</th></tr>",
        f"<tr><td>Просрочены</td><td>{s['expired']}</td></tr>",
        f"<tr><td>Использовать в ближайшие дни</td><td>{s['urgent']}</td></tr>",
        f"<tr><td>Без даты срока</td><td>{s['no_date']}</td></tr>",
        f"<tr><td>Вне активного учёта</td><td>{s['ignored']}</td></tr>",
        "</table>",
    ]
    for title, key, note in [
        ("Просроченные продукты", "expired", "Не использовать без проверки безопасности и решения пользователя."),
        ("Продукты с приближающимся сроком", "urgent", "Рекомендуется включить в ближайшее меню."),
        ("Активные продукты без даты срока", "no_date", "Нужно уточнить срок вручную, если он критичен для безопасности."),
    ]:
        parts += [f"<h2>{_esc(title)}</h2>", f"<blockquote>{_esc(note)}</blockquote>"]
        items = result["groups"][key]
        if not items:
            parts.append("<p>Нет позиций.</p>")
            continue
        parts.append("<table><tr><th>Продукт</th><th>Остаток</th><th>Место</th>"
                     "<th>Срок</th><th>Осталось дней</th></tr>")
        for item in items:
            days = "—" if item["days_left"] is None else item["days_left"]
            expiry = item["expires_at"] or "—"
            parts.append(
                "<tr>"
                f"<td>{_esc(item['name'])}</td>"
                f"<td>{_esc(item['quantity'])} {_esc(item['unit'])}</td>"
                f"<td>{_esc(item['location'])}</td>"
                f"<td>{_esc(expiry)}</td>"
                f"<td>{_esc(days)}</td>"
                "</tr>"
            )
        parts.append("</table>")
    ignored_items = sorted(result["groups"]["ignored"], key=lambda x: x["name"])
    if ignored_items:
        # Зеркало MD-секции «Требуется ручной учёт» (п.10 ревью).
        parts += [
            "<h2>Требуется ручной учёт</h2>",
            (
                "<blockquote>Позиции вне активного учёта: остаток нулевой "
                "(POS-справочники импортируются без остатков и сроков) или статус "
                "исключает позицию. Подтвердите остаток и срок вручную, чтобы "
                "позиция попала в расчёт.</blockquote>"
            ),
            (
                "<table><tr><th>Продукт</th><th>Остаток</th><th>Место</th>"
                "<th>Статус</th></tr>"
            ),
        ]
        for item in ignored_items:
            parts.append(
                "<tr>"
                f"<td>{_esc(item['name'])}</td>"
                f"<td>{_esc(item['quantity'])} {_esc(item['unit'])}</td>"
                f"<td>{_esc(item['location'])}</td>"
                f"<td>{_esc(item['status'])}</td>"
                "</tr>"
            )
        parts.append("</table>")
    if menu_ideas is not None:
        notices, ideas = menu_ideas
        parts += ["<h2>Идеи блюд (DeepSeek)</h2>"]
        for notice in notices:
            # notices написаны в Markdown («**2**») — для HTML убираем разметку.
            parts.append(f"<blockquote>{_esc(notice).replace('**', '')}</blockquote>")
        if ideas.strip():
            parts.append(_ideas_to_html(ideas))
    parts += [
        "<footer>Сгенерировано SmartKitchen Family CLI Agent</footer>",
        "</body>", "</html>",
    ]
    return "\n".join(parts)


def load_family_allergens(profile: Path, *, allow_no_profile: bool = False) -> tuple[set[str], list[str]]:
    """Загружает аллергены профиля семьи для hard-filter LLM-подсказок.

    Возвращает ``(аллергены, предупреждения)``.

    Профиль — часть защитного слоя (cli_agent_design.md: «если API ошибся, CLI
    не пропустит опасный рецепт»), поэтому его отсутствие больше не отключает
    фильтр молча:

    * файла нет и ``allow_no_profile`` не задан → ``ValueError`` (запуск
      завершается с кодом 2 и понятной инструкцией);
    * файла нет и задан ``--allow-no-profile`` → пустой набор плюс громкое
      предупреждение в отчёт и в stderr;
    * файл есть, но аллергенов в нём нет → тот же громкий предупреждающий
      путь: фильтр формально загружен, но ничего не скрывает.

    Асимметрия намеренная: пустой список ``allergens`` в существующем файле —
    это осознанное заявление «в семье нет аллергий», а вот отсутствующий файл
    чаще всего означает неверный путь или потерянный том в контейнере, поэтому
    без явного флага запуск прерывается.

    Ошибки формата профиля поднимает ``agent.allergens.load_allergens``.
    """
    # Импорт внутри функции: agent.allergens → agent.adapters.base → agent.cli,
    # на уровне модуля получилась бы циклическая зависимость.
    from agent.allergens import load_allergens

    if not profile.exists():
        if not allow_no_profile:
            raise ValueError(
                f"Профиль семьи с аллергенами не найден: {profile}. "
                "Без него hard-filter аллергенов отключается, а публиковать "
                "LLM-подсказки о еде без фильтра небезопасно. Создайте файл "
                'вида {"family_id": "demo", "allergens": ["орехи", "мёд"]} '
                "или добавьте --allow-no-profile, чтобы осознанно принять риск "
                "(в отчёте появится явное предупреждение)."
            )
        return set(), [
            (
                "⚠️ Hard-filter аллергенов ОТКЛЮЧЁН: профиль семьи не найден "
                f"({profile}). Подсказки ниже НЕ проверены на аллергены "
                "(подтверждено флагом --allow-no-profile)."
            )
        ]
    allergens = load_allergens(profile)
    if not allergens:
        return set(), [
            (
                f"⚠️ Hard-filter аллергенов фактически не работает: профиль {profile} "
                "не содержит ни одного аллергена (поле «allergens» пустое). "
                "Подсказки ниже НЕ проверены на аллергены."
            )
        ]
    return allergens, []


def main() -> int:
    parser = argparse.ArgumentParser(description="SmartKitchen Family deterministic CLI agent")
    parser.add_argument("--inventory", type=Path, default=Path("data/inventory.json"),
                        help="JSON-файл инвентаря (источник «json»)")
    parser.add_argument("--source", default="json",
                        help="источник данных: json или " + ", ".join(available_sources()))
    parser.add_argument("--out", type=Path, default=Path("reports/expiry_report.md"))
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())  # noqa: DTZ011 — дата расчёта по умолчанию = локальный «сегодня»
    parser.add_argument("--warning-days", type=int, default=3)
    parser.add_argument("--suggest-menu", action="store_true",
                        help="добавить в отчёт идеи блюд от DeepSeek (нужен DEEPSEEK_API_KEY)")
    parser.add_argument("--strict-llm", action="store_true",
                        help="считать недоступность DeepSeek фатальной (exit 2, без записи "
                             "отчёта). По умолчанию отчёт о сроках публикуется, а сбой LLM "
                             "фиксируется в секции идей и в stderr")
    parser.add_argument("--profile", type=Path, default=Path("data/family_profile.json"),
                        help="профиль семьи с аллергенами (hard-filter LLM-подсказок)")
    parser.add_argument("--allow-no-profile", action="store_true",
                        help="разрешить --suggest-menu без профиля аллергенов: фильтр будет "
                             "отключён, а в отчёте появится явное предупреждение")
    parser.add_argument("--html-out", type=Path, default=None,
                        help="дополнительно записать отчёт как автономную HTML-страницу")
    args = parser.parse_args()
    if args.warning_days < 0:
        parser.error("--warning-days должен быть неотрицательным")
    # Проверяем профиль ДО чтения инвентаря и тем более до запроса к LLM:
    # ошибка конфигурации безопасности не должна обнаруживаться после сети.
    allergens: set[str] = set()
    allergen_warnings: list[str] = []
    if args.suggest_menu:
        from agent.adapters.base import AdapterError
        try:
            allergens, allergen_warnings = load_family_allergens(
                args.profile, allow_no_profile=args.allow_no_profile
            )
        except (ValueError, AdapterError) as exc:
            parser.error(str(exc))
        for warning in allergen_warnings:
            print(warning, file=sys.stderr)
    if args.source == "json":
        try:
            products = load_products(args.inventory)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        try:
            products = load_source(args.source)
        except Exception as exc:  # noqa: BLE001 — любой сбой адаптера = понятная ошибка запуска
            parser.error(f"Источник «{args.source}» недоступен: {exc}")
    result = analyze(products, args.as_of, args.warning_days)
    markdown = render_markdown(result)
    # Секция идей в виде (notices, body): один источник для MD- и HTML-рендера,
    # иначе HTML, который публикуется на хостинг по cron, теряет идеи (п.9 ревью).
    menu_ideas: tuple[list[str], str] | None = None
    if args.suggest_menu:
        from agent.adapters.deepseek import suggest_menu_ideas
        from agent.allergens import (
            expand_allergens,
            filter_suggestions,
            matched_profile_allergens,
        )
        urgent = result["groups"]["urgent"]
        candidates = [p for p in products
                      if p.name in {item["name"] for item in urgent}]
        notices = [
            (
                "⚠️ Неподтверждённая LLM-подсказка: проверьте состав и аллергены "
                "вручную перед приготовлением."
            )
        ]
        notices += allergen_warnings
        try:
            ideas = suggest_menu_ideas(candidates, allergens=sorted(allergens))
        except Exception as exc:  # noqa: BLE001 — п.6: любой сбой LLM не роняет отчёт
            if args.strict_llm:
                parser.error(f"DeepSeek недоступен: {exc}")
            # П.6 ревью: детерминированный отчёт важнее LLM-подсказок. Публикуем
            # отчёт о сроках, а сбой фиксируем в секции идей и в stderr (лог cron).
            error = f"DeepSeek недоступен: {exc}"
            print(f"⚠️ {error} Отчёт о сроках записан без идей блюд.", file=sys.stderr)
            notices = [f"⚠️ {error}", "Идеи блюд не получены; отчёт о сроках полный."]
            ideas = ""
        else:
            # Hard-filter аллергенов: блюда с аллергенами из профиля семьи
            # не показываются вовне (cli_agent_design.md, раздел про аллергены).
            # Фильтр работает по расширенному набору (профиль + синонимы из
            # data/allergen_synonyms.json), а в отчёте называются слова профиля.
            raw_ideas = ideas
            ideas, dropped = filter_suggestions(ideas, expand_allergens(allergens))
            if dropped:
                reasons = matched_profile_allergens(raw_ideas, allergens)
                suffix = f" ({', '.join(reasons)})" if reasons else ""
                notices.append(f"Скрыто блюд с аллергенами профиля: **{dropped}**{suffix}.")
            if not ideas.strip():
                ideas = "Нет блюд без аллергенов профиля семьи."
        markdown += (
            "\n## Идеи блюд (DeepSeek)\n\n"
            + "> " + "\n> ".join(notices) + "\n\n"
            + ideas.strip() + "\n"
        )
        menu_ideas = (notices, ideas)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown, encoding="utf-8")
    if args.html_out:
        args.html_out.parent.mkdir(parents=True, exist_ok=True)
        args.html_out.write_text(render_html(result, menu_ideas=menu_ideas), encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
