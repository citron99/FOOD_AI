"""Детерминированный hard-filter аллергенов для LLM-подсказок.

Согласно cli_agent_design.md (раздел «Аллергены как hard-filter»): даже если
LLM предложила блюдо, содержащее аллерген из профиля семьи, оно НЕ показывается
пользователю вовсе. Это второе мнение поверх LLM: фильтр работает по простому
правилу без ML, поэтому его поведение предсказуемо и покрыто тестами.

Профиль семьи — JSON-файл:

    {"family_id": "demo", "allergens": ["орехи", "мёд"]}

Правила сравнения (все — в пользу перестраховки, см. «Консервативность»):

1. Текст и аллергены нормализуются: нижний регистр и обязательная замена
   «ё» → «е». Без неё аллерген «мёд» не ловит «медом»/«медовик», а именно так
   LLM и люди пишут чаще всего — это был бы опасный пропуск.
2. Сравнение идёт по словам. Многословный аллерген («коровье молоко»)
   трактуется как набор слов: блюдо скрывается, если совпало ЛЮБОЕ из них.
3. Слово аллергена длиной 3 символа и больше сравнивается по основе
   (первые 4 символа): «орехи» ловит «орехами», «ореховый»; «мед» ловит
   «медом», «медовик».
4. Слово аллергена короче 3 символов сравнивается ТОЛЬКО точно. Иначе
   одиночные русские слова («а», «и», «с», «к», «у», «не», «на») совпадали бы
   с основой почти любого аллергена и скрывали безопасные блюда. Точное
   совпадение короткого слова остаётся (фильтр защищающий, пропуск хуже):
   если в профиль записали союз «и», будут скрыты все блюда со словом «и».
   Поэтому в профиль нужно писать только содержательные слова-аллергены.
5. Синонимы и производные («глютен» → «пшеница», «мука», «манка»…) берутся из
   ``data/allergen_synonyms.json`` — см. :func:`load_synonyms`.

Консервативность намеренная: ложно скрытое блюдо — неудобство, ложно показанное
блюдо с аллергеном — риск для здоровья. Поэтому, например, «коровье молоко»
скроет и «королевские креветки» (совпадение по «коро»), а «мед» — «медальон».
Если такое огрубление мешает, укажите в профиле более точное слово
(«молоко» вместо «коровье молоко»).

Из той же логики следует ИЛИ-семантика многословных аллергенов: она скрывает
строго больше, чем И-семантика, а значит не может пропустить аллерген.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

from agent.adapters.base import AdapterError

_WORD_RE = re.compile(r"[а-яa-z0-9]+")

# Слово аллергена короче этого сравнивается только точно (защита от союзов «а», «и», «с»).
MIN_PREFIX_LENGTH = 3
# Длина основы для сравнения словоформ.
STEM_LENGTH = 4

# Словарь синонимов лежит рядом с демо-данными проекта (копируется в Docker-образ).
DEFAULT_SYNONYMS_PATH = Path(__file__).resolve().parents[1] / "data" / "allergen_synonyms.json"


def normalize(text: str) -> str:
    """Нижний регистр и замена «ё» на «е» — единая форма для текста и аллергенов."""
    return str(text).lower().replace("ё", "е")


def _words(text: str) -> list[str]:
    """Слова нормализованного текста (буквы и цифры, пунктуация отбрасывается)."""
    return _WORD_RE.findall(normalize(text))


def _forms(term: str) -> list[tuple[str, bool]]:
    """Формы слова аллергена: (основа, сравнивать_только_точно).

    Для коротких слов (меньше ``MIN_PREFIX_LENGTH``) префиксное сравнение
    отключается — иначе союз «а» совпадал бы с основой «арах» от «арахис».
    """
    forms: list[tuple[str, bool]] = []
    for word in _words(term):
        if len(word) < MIN_PREFIX_LENGTH:
            forms.append((word, True))
        else:
            forms.append((word[:STEM_LENGTH], False))
    return forms


def load_allergens(path: Path) -> set[str]:
    """Читает профиль семьи и возвращает множество аллергенов (нормализованных).

    Значения приводятся к нижнему регистру с заменой «ё» → «е», поэтому
    «МЁД» возвращается как «мед». Пустые и непечатные элементы пропускаются.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AdapterError(f"Профиль семьи не найден: {path}") from None
    except json.JSONDecodeError as exc:
        raise AdapterError(f"Профиль семьи не является корректным JSON: {path} ({exc})") from None
    if not isinstance(payload, dict):
        raise AdapterError(f"Профиль семьи в {path} должен быть объектом с полем «allergens»")
    allergens = payload.get("allergens", [])
    if not isinstance(allergens, list):
        raise AdapterError(f"Поле «allergens» в {path} должно быть списком")
    result = set()
    for item in allergens:
        normalized = normalize(item).strip()
        if normalized:
            result.add(normalized)
    return result


def load_synonyms(path: Path = DEFAULT_SYNONYMS_PATH) -> dict[str, set[str]]:
    """Читает словарь «аллерген → производные и синонимы».

    Формат файла: ``{"глютен": ["пшеница", "мука"], ...}``. Ключи и значения
    нормализуются. Отсутствующий файл не ошибка: фильтр продолжит работать
    только по словам профиля (словарь — усиление, а не обязательная часть).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise AdapterError(f"Не удалось прочитать словарь синонимов аллергенов {path}: {exc}") from None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"Словарь синонимов аллергенов не является корректным JSON: {path} ({exc})") from None
    if not isinstance(payload, dict):
        raise AdapterError(f"Словарь синонимов в {path} должен быть объектом «аллерген: [слова]»")
    synonyms: dict[str, set[str]] = {}
    for key, values in payload.items():
        allergen = normalize(key).strip()
        if not allergen or allergen.startswith("_"):
            continue  # служебные ключи вроде "_comment"
        if not isinstance(values, list):
            raise AdapterError(
                f"Значение для аллергена «{allergen}» в {path} должно быть списком слов"
            )
        words = {normalize(value).strip() for value in values}
        synonyms[allergen] = {word for word in words if word}
    return synonyms


def expand_allergens(allergens: Iterable[str], synonyms: dict[str, set[str]] | None = None) -> set[str]:
    """Дополняет аллергены профиля их синонимами и производными.

    «глютен» сам по себе не ловит «пасту из пшеницы с мукой» — словарь синонимов
    закрывает этот смысловой пробел. Неизвестные в словаре аллергены остаются
    как есть.
    """
    table = load_synonyms() if synonyms is None else synonyms
    expanded: set[str] = set()
    for allergen in allergens:
        normalized = normalize(allergen).strip()
        if not normalized:
            continue
        expanded.add(normalized)
        expanded.update(table.get(normalized, set()))
        # Многословный аллерген: синонимы ищем и по отдельным словам
        # («коровье молоко» → ключ «молоко» в словаре).
        for word in _words(normalized):
            expanded.update(table.get(word, set()))
    return expanded


def contains_allergen(text: str, allergens: Iterable[str]) -> bool:
    """Возвращает True, если текст содержит любой из аллергенов.

    Сравнение без учёта регистра и «ё», по словам, с учётом русских словоформ
    (основа первых 4 символов) — см. правила в докстроке модуля.
    """
    words = _words(text)
    if not words:
        return False
    for allergen in allergens:
        for form, exact_only in _forms(allergen):
            for word in words:
                hit = word == form if exact_only else word.startswith(form)
                if hit:
                    return True
    return False


def matched_allergens(text: str, allergens: Iterable[str]) -> list[str]:
    """Какие именно термины из ``allergens`` найдены в тексте.

    Термины берутся как есть, поэтому при передаче расширенного набора
    в списке окажутся и синонимы («медовый» рядом с «мед»). Для сообщений
    пользователю используйте :func:`matched_profile_allergens`.
    """
    found = []
    for allergen in allergens:
        if contains_allergen(text, {allergen}):
            found.append(str(allergen))
    return sorted(found)


def matched_profile_allergens(text: str, profile: Iterable[str],
                              synonyms: dict[str, set[str]] | None = None) -> list[str]:
    """Аллергены ПРОФИЛЯ, из-за которых текст скрыт (с учётом их синонимов).

    Возвращает только те слова, что пользователь записал в профиль семьи, —
    так в отчёте читается «скрыто из-за: глютен», а не «из-за: сейтан».
    """
    table = load_synonyms() if synonyms is None else synonyms
    found = []
    for allergen in profile:
        normalized = normalize(allergen).strip()
        if not normalized:
            continue
        if contains_allergen(text, expand_allergens({normalized}, table)):
            found.append(normalized)
    return sorted(found)


def filter_suggestions(text: str, allergens: Iterable[str]) -> tuple[str, int]:
    """Отбрасывает строки подсказок с аллергенами.

    Возвращает (отфильтрованный текст, число скрытых строк). Непустые строки,
    не содержащие аллергенов, сохраняются в исходном порядке. Пустой набор
    аллергенов ничего не скрывает — но тогда профиль не загружен, и вызывающий
    код обязан это явно отметить (см. ``--allow-no-profile`` в CLI).
    """
    allergen_list = list(allergens)
    kept: list[str] = []
    dropped = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if allergen_list and contains_allergen(stripped, allergen_list):
            dropped += 1
            continue
        kept.append(stripped)
    return "\n".join(kept), dropped
