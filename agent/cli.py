"""SmartKitchen Family CLI agent.

The first MVP task is intentionally deterministic: identify products that are
expired or approaching expiry and produce a safe, auditable report. No LLM is
required, so the agent works without external keys.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Product:
    name: str
    quantity: float
    unit: str
    location: str
    expires_at: date | None
    status: str = "active"


def parse_date(value: str | None, name: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(
            f"Неверный формат даты у продукта «{name}»: {value!r} (нужно ГГГГ-ММ-ДД)"
        ) from None


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
        raise ValueError(f"Неверная структура инвентаря в {path}: ожидается объект с полем «products» или список")
    if not isinstance(raw_items, list):
        raise ValueError(f"Поле «products» в {path} должно быть списком")
    products: list[Product] = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError(f"Каждая позиция инвентаря должна быть объектом: {item!r}")
        name = str(item.get("name", "без названия"))
        if "name" not in item:
            raise ValueError("В позиции инвентаря отсутствует обязательное поле «name»")
        if "quantity" not in item:
            raise ValueError(f"В позиции инвентаря отсутствует обязательное поле «quantity»: {name}")
        try:
            quantity = float(item["quantity"])
        except (TypeError, ValueError):
            raise ValueError(f"Нечисловой остаток у продукта «{name}»: {item['quantity']!r}") from None
        if quantity < 0:
            raise ValueError(f"Отрицательный остаток запрещён: {name}")
        products.append(Product(
            name=str(item["name"]),
            quantity=quantity,
            unit=str(item.get("unit", "шт.")),
            location=str(item.get("location", "не указано")),
            expires_at=parse_date(item.get("expires_at"), name),
            status=str(item.get("status", "active")),
        ))
    return products


def classify(product: Product, today: date, warning_days: int) -> str:
    if product.status not in {"active", "frozen"} or product.quantity <= 0:
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
            lines.append(f"| {item['name']} | {item['quantity']} {item['unit']} | {item['location']} | {expiry} | {days} |")
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


def render_html(result: dict[str, Any]) -> str:
    """Автономная HTML-страница отчёта без внешних зависимостей.

    Нужна для публикации отчёта на хостинге (например, по cron на Beget),
    где нет библиотек конвертации Markdown.
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
    parts += [
        "<footer>Сгенерировано SmartKitchen Family CLI Agent</footer>",
        "</body>", "</html>",
    ]
    return "\n".join(parts)


def main() -> int:
    from agent.adapters.registry import available_sources, load_source

    parser = argparse.ArgumentParser(description="SmartKitchen Family deterministic CLI agent")
    parser.add_argument("--inventory", type=Path, default=Path("data/inventory.json"),
                        help="JSON-файл инвентаря (источник «json»)")
    parser.add_argument("--source", default="json",
                        help="источник данных: json или " + ", ".join(available_sources()))
    parser.add_argument("--out", type=Path, default=Path("reports/expiry_report.md"))
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--warning-days", type=int, default=3)
    parser.add_argument("--suggest-menu", action="store_true",
                        help="добавить в отчёт идеи блюд от DeepSeek (нужен DEEPSEEK_API_KEY)")
    parser.add_argument("--profile", type=Path, default=Path("data/family_profile.json"),
                        help="профиль семьи с аллергенами (hard-filter LLM-подсказок)")
    parser.add_argument("--html-out", type=Path, default=None,
                        help="дополнительно записать отчёт как автономную HTML-страницу")
    args = parser.parse_args()
    if args.warning_days < 0:
        parser.error("--warning-days должен быть неотрицательным")
    if args.source == "json":
        try:
            products = load_products(args.inventory)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        try:
            products = load_source(args.source)
        except Exception as exc:
            parser.error(f"Источник «{args.source}» недоступен: {exc}")
    result = analyze(products, args.as_of, args.warning_days)
    markdown = render_markdown(result)
    if args.suggest_menu:
        from agent.adapters.base import AdapterError
        from agent.adapters.deepseek import suggest_menu_ideas
        from agent.allergens import filter_suggestions, load_allergens
        urgent = result["groups"]["urgent"]
        candidates = [p for p in products
                      if p.name in {item["name"] for item in urgent}]
        try:
            ideas = suggest_menu_ideas(candidates)
        except Exception as exc:
            parser.error(f"DeepSeek недоступен: {exc}")
        # Hard-filter аллергенов: блюда с аллергенами из профиля семьи
        # не показываются вовне (cli_agent_design.md, раздел про аллергены).
        if args.profile.exists():
            try:
                allergens = load_allergens(args.profile)
            except AdapterError as exc:
                parser.error(str(exc))
            ideas, dropped = filter_suggestions(ideas, allergens)
        else:
            allergens, dropped = set(), 0
        notice = (
            "> ⚠️ Неподтверждённая LLM-подсказка: проверьте состав и аллергены "
            "вручную перед приготовлением."
        )
        if dropped:
            notice += f"\n>\n> Скрыто блюд с аллергенами профиля: **{dropped}**."
        if not ideas.strip():
            ideas = "Нет блюд без аллергенов профиля семьи."
        markdown += (
            "\n## Идеи блюд (DeepSeek)\n\n"
            + notice + "\n\n"
            + ideas.strip() + "\n"
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown, encoding="utf-8")
    if args.html_out:
        args.html_out.parent.mkdir(parents=True, exist_ok=True)
        args.html_out.write_text(render_html(result), encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
