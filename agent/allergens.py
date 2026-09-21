"""Детерминированный hard-filter аллергенов для LLM-подсказок.

Согласно cli_agent_design.md (раздел «Аллергены как hard-filter»): даже если
LLM предложила блюдо, содержащее аллерген из профиля семьи, оно НЕ показывается
пользователю вовсе. Это второе мнение поверх LLM: фильтр работает по простому
правилу без ML, поэтому его поведение предсказуемо и покрыто тестами.

Профиль семьи — JSON-файл:

    {"family_id": "demo", "allergens": ["орехи", "мёд"]}

Правило срабатывания: если в строке подсказки (блюдо + состав) встречается
аллерген (без учёта регистра и с учётом русских словоформ: сравнение идёт по
основе первых 4 символов, поэтому «орехи» ловит и «орехами»), строка
отбрасывается целиком. Фильтр намеренно консервативен: при сомнении блюдо
скрывается, а не показывается.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from agent.adapters.base import AdapterError

_WORD_RE = re.compile(r"[а-яa-zё0-9]+")


def load_allergens(path: Path) -> set[str]:
    """Читает профиль семьи и возвращает множество аллергенов (в нижнем регистре)."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AdapterError(f"Профиль семьи не найден: {path}") from None
    except json.JSONDecodeError as exc:
        raise AdapterError(f"Профиль семьи не является корректным JSON: {path} ({exc})") from None
    allergens = payload.get("allergens", [])
    if not isinstance(allergens, list):
        raise AdapterError(f"Поле «allergens» в {path} должно быть списком")
    result = set()
    for item in allergens:
        normalized = str(item).strip().lower()
        if normalized:
            result.add(normalized)
    return result


def contains_allergen(text: str, allergens: set[str]) -> bool:
    """Возвращает True, если текст содержит любой аллерген.

    Сравнение без учёта регистра и с учётом русских словоформ: слово считается
    совпадением, если оно начинается с основы аллергена (первые 4 символа)
    или полностью совпадает с ней — «орехи» ловит «орехами», «мёд» — «мёдом».
    """
    words = _WORD_RE.findall(text.lower())
    for allergen in allergens:
        stem = allergen[:4]
        for word in words:
            if word.startswith(stem) or stem.startswith(word):
                return True
    return False


def filter_suggestions(text: str, allergens: set[str]) -> tuple[str, int]:
    """Отбрасывает строки подсказок с аллергенами.

    Возвращает (отфильтрованный текст, число скрытых строк). Непустые строки,
    не содержащие аллергенов, сохраняются в исходном порядке.
    """
    kept: list[str] = []
    dropped = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if allergens and contains_allergen(stripped, allergens):
            dropped += 1
            continue
        kept.append(stripped)
    return "\n".join(kept), dropped
