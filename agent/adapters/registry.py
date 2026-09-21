"""Реестр источников данных для CLI-агента.

Каждый источник — система, из которой можно загрузить справочник продуктов.
Ключи реестра используются в флаге ``--source`` CLI:

    python -m agent.cli --source iiko ...

Добавление новой интеграции = новый модуль в agent/adapters/ + одна строка
в SOURCES. Все адаптеры читают секреты только из окружения.
"""
from __future__ import annotations

from typing import Callable

from agent.adapters import frontpad, iiko, quickresto, rkeeper, saby, yuma
from agent.adapters.base import AdapterError, ImportOutcome
from agent.cli import Product


def _products(outcome: object) -> list[Product]:
    """Нормализует результат адаптера (ImportOutcome/ImportResult) в список."""
    products = getattr(outcome, "products", None)
    if products is None:
        raise AdapterError(f"Адаптер вернул результат без списка продуктов: {outcome!r}")
    return list(products)


def _from_rkeeper() -> list[Product]:
    return _products(rkeeper.import_products(rkeeper.RkeeperConfig.from_env()))


def _from_iiko() -> list[Product]:
    return _products(iiko.import_products())


def _from_quickresto() -> list[Product]:
    return _products(quickresto.import_products())


def _from_frontpad() -> list[Product]:
    return _products(frontpad.import_products())


def _from_saby() -> list[Product]:
    return _products(saby.import_products())


def _from_yuma() -> list[Product]:
    return _products(yuma.import_products())


SOURCES: dict[str, Callable[[], list[Product]]] = {
    "rkeeper": _from_rkeeper,
    "iiko": _from_iiko,
    "quickresto": _from_quickresto,
    "frontpad": _from_frontpad,
    "saby": _from_saby,
    "yuma": _from_yuma,
}


def available_sources() -> list[str]:
    return sorted(SOURCES)


def load_source(name: str) -> list[Product]:
    """Загружает справочник из named-источника или бросает понятную ошибку."""
    try:
        loader = SOURCES[name]
    except KeyError:
        raise AdapterError(
            f"Неизвестный источник «{name}». Доступные: {', '.join(available_sources())}"
        ) from None
    return loader()
