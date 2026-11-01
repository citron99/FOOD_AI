"""Общие модели данных детерминированного ядра SmartKitchen Family.

Это самый нижний слой зависимостей: ``models ← adapters ← cli``.
Волна 5 (ревью 28.09.2026, п.13): ``Product`` и ``parse_date`` раньше жили
в ``agent.cli``, поэтому все адаптеры и hard-filter аллергенов зависели от
CLI-модуля, а ``cli.py`` вынужден был лениво импортировать реестр адаптеров
внутри ``main()``. Перенос сюда убирает цикл импортов и делает слои
направленными: модель не знает ни об адаптерах, ни о CLI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


class InvalidDateError(ValueError):
    """Неверный формат даты у продукта."""

    def __init__(self, name: str, value: object) -> None:
        super().__init__(
            f"Неверный формат даты у продукта «{name}»: {value!r} (нужно ГГГГ-ММ-ДД)"
        )


@dataclass(frozen=True)
class Product:
    name: str
    quantity: float
    unit: str
    location: str
    expires_at: date | None
    status: str = "active"


_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_date(value: object, name: str) -> date | None:
    """Парсит дату ГГГГ-ММ-ДД; None/пустая строка означают «даты нет».

    Волна 3 (п. «валидация входных данных» ревью): нестроковые значения
    (число ``20260820``, список ``["2026"]``) раньше доезжали до
    ``datetime.strptime`` и давали сырой ``TypeError`` без имени продукта.
    Теперь это тот же ``ValueError``, что и для строки в неверном формате.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        # Волна 3: единый ValueError с именем продукта для любого неверного
        # формата даты (тесты и README зафиксировали ValueError, не TypeError).
        raise InvalidDateError(name, value)
    # Контракт поля — только календарная дата ГГГГ-ММ-ДД, а date.fromisoformat
    # с Python 3.11 принимает ещё компактную (20260820) и недельную запись.
    if not _DATE_PATTERN.fullmatch(value):
        raise InvalidDateError(name, value)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InvalidDateError(name, value) from None
