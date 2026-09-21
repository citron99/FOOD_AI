# Дизайн CLI-агента для SmartKitchen Family

**Версия:** 0.1 (черновик)
**Дата:** 2026-08-18
**Автор:** Дмитрий Фролов
**Назначение:** Интерактивный CLI для работы с рецептами и меню через REST API проекта SmartKitchen Family.
**Режим:** REPL + одноразовые команды.
**Стек:** Python 3.11+, Typer, Rich, httpx, Pydantic v2, prompt_toolkit.

---

## 1. Цели и границы

### Входит в scope

1. Подключение к работающему REST API SmartKitchen Family (по `SK_API_URL` + `SK_API_TOKEN`).
2. Просмотр и поиск рецептов из каталога.
3. Подбор рецепта под текущий инвентарь семьи (use case 3 из ТЗ: «Что приготовить из того, что есть?»).
4. Фильтрация по аллергенам и запрещённым продуктам активного профиля.
5. Формирование недельного меню (use case 4) и просмотр списка покупок.
6. Списание ингредиентов при «приготовлении» рецепта (use case 5).
7. Интерактивная сессия с историей и контекстом (REPL).

### Не входит в scope

1. OCR-обработка чеков — только через UI/Telegram.
2. Управление пользователями, ролями, приглашениями.
3. Запись произвольных операций в БД в обход API (CLI не должен иметь прямого доступа к PostgreSQL/SQLite).
4. Платежи, интеграция с магазинами, штрихкоды, голос.
5. Изменение рецептов каталога (только чтение + выбор).
6. Деплой, миграции Alembic, бэкапы — это задачи отдельной CLI-утилиты `sk-admin`.

---

## 2. Архитектура

### Слои

```
┌─────────────────────────────────────────────────┐
│ REPL / command layer       (Typer + prompt)     │
│ - разбор ввода, история, автокомплит            │
├─────────────────────────────────────────────────┤
│ Session / context          (in-memory state)    │
│ - family_id, profile_id, фильтры, last_recipes  │
├─────────────────────────────────────────────────┤
│ Domain services            (чистые функции)     │
│ - recipe_ranker, allergen_filter, menu_builder │
├─────────────────────────────────────────────────┤
│ API client                 (httpx + retry)      │
│ - auth, rate limit, ошибки, типизированные      │
│   ответы через Pydantic                         │
├─────────────────────────────────────────────────┤
│ Transport                  (HTTP/REST)          │
│ - SK_API_URL, SK_API_TOKEN                      │
└─────────────────────────────────────────────────┘
```

### Граница ответственности

- CLI **не знает про БД**, не импортирует модели SQLAlchemy проекта, не лезет в файловую систему за рецептами.
- Вся бизнес-логика — на стороне API. CLI делает запрос и интерпретирует ответ.
- Детерминированная фильтрация (аллергены, остатки < 0, дубликаты) делается и в CLI как **защитный слой**: если API ошибся, CLI не пропустит опасный рецепт.

---

## 3. Структура проекта (внутри `C:\111\AI_Food\src\smartkitchen\cli\`)

```
cli/
├── __init__.py
├── __main__.py            # entry: python -m smartkitchen.cli
├── app.py                 # Typer app, регистрация команд
├── config.py              # SK_API_URL, SK_API_TOKEN, timeouts, page_size
├── client/
│   ├── __init__.py
│   ├── http.py            # httpx.AsyncClient, retry, auth header
│   ├── errors.py          # APIError, AuthError, RateLimitError, AllergenViolation
│   └── models.py          # Pydantic: Recipe, Ingredient, Profile, MenuPlan, ShoppingList
├── services/
│   ├── __init__.py
│   ├── recipes.py         # search, rank_by_inventory, filter_allergens
│   ├── menu.py            # generate_week_menu, shopping_list_from_menu
│   └── inventory.py       # get_current_inventory, deduct_after_cook
├── session/
│   ├── __init__.py
│   ├── state.py           # SessionState dataclass
│   └── repl.py            # prompt_toolkit REPL, history file, автокомплит
├── ui/
│   ├── __init__.py
│   ├── table.py           # Rich-таблицы для рецептов и меню
│   └── format.py          # форматирование %, отсутствий, цен
└── commands/
    ├── __init__.py
    ├── auth.py            # login, whoami, switch-family
    ├── recipes.py         # find, show, rank, cook
    ├── menu.py            # plan, show, shopping
    └── inventory.py       # list, expiring
```

Соглашения по неймингу — из `claude.md`:
- модули/файлы: `snake_case.py`,
- классы/Pydantic/исключения: `PascalCase`,
- функции: глаголы в `snake_case`,
- константы: `UPPER_SNAKE_CASE`,
- тесты: `test_<module>.py`.

---

## 4. Конфигурация и секреты

### Переменные окружения

| Переменная | Назначение | Обязательная |
|---|---|---|
| `SK_API_URL` | Базовый URL REST API (например, `http://localhost:8000`) | да |
| `SK_API_TOKEN` | Bearer-токен пользователя (получен через UI/Telegram) | да |
| `SK_FAMILY_ID` | ID семьи по умолчанию | нет |
| `SK_PROFILE_ID` | ID активного профиля (для аллергенов) | нет |
| `SK_TIMEOUT_S` | Таймаут HTTP-запроса (default `10`) | нет |
| `SK_HISTORY_FILE` | Путь к файлу истории REPL (default `~/.sk_cli_history`) | нет |

Секреты читаются **только из окружения**, никогда не из CLI-флагов и не из конфигов в репозитории. Если `SK_API_TOKEN` не задан — CLI завершается с понятной ошибкой и инструкцией.

### Файл конфигурации (опционально)

`~/.sk_cli.toml` — только для несекретных настроек (`SK_API_URL`, `SK_FAMILY_ID`, предпочтения UI). Если переменные окружения заданы — они имеют приоритет.

---

## 5. Команды (Typer + REPL)

### 5.1 Одноразовые (one-shot)

| Команда | Назначение |
|---|---|
| `sk recipes find "курица"` | Поиск рецепта по строке |
| `sk recipes rank --top 10` | Топ рецептов под текущий инвентарь |
| `sk recipes show <recipe_id>` | Карточка рецепта: ингредиенты, шаги, КБЖУ, время |
| `sk recipes cook <recipe_id>` | Подтвердить приготовление, списать ингредиенты |
| `sk menu plan --days 7` | Сгенерить недельное меню |
| `sk menu show <plan_id>` | Показать план |
| `sk menu shopping <plan_id>` | Получить список покупок (с учётом остатков) |
| `sk inventory list` | Текущий инвентарь |
| `sk inventory expiring` | Что использовать в первую очередь |
| `sk auth whoami` | Текущий пользователь, семья, профиль |
| `sk auth switch-family <id>` | Сменить активную семью |
| `sk auth switch-profile <id>` | Сменить профиль (для аллергенов) |

Все команды возвращают машиночитаемый JSON при `--json` и человекочитаемый вывод (Rich-таблицы) по умолчанию.

### 5.2 Интерактивный REPL (`sk repl`)

REPL запускается без аргументов и предоставляет сокращённые команды с автокомплитом и историей.

**Сессионные команды:**

```
help                              список команд
status                           текущая семья / профиль / фильтры
use family <id>                  переключить семью
use profile <id>                 переключить профиль
filters                          показать активные фильтры (аллергены, время, цель)
set max-time <minutes>           ограничение по времени готовки
set goal <low-waste|cheap|fast>  цель подбора
unset <filter>                   сбросить фильтр
recipes find <query>             поиск
recipes rank [N]                 топ-N рецептов под инвентарь
recipes show <id>                карточка
cook <recipe_id>                 приготовить → запрос подтверждения → списание
menu plan [days]                 сгенерить меню
menu show <plan_id>
menu shopping <plan_id>
inventory expiring
inventory list
history                          последние 20 команд
exit / quit / Ctrl-D             выход
```

**Сессионное состояние** (в памяти, не персистится между запусками):

- `active_family_id`
- `active_profile_id`
- `filters: dict` — `max_time_min`, `goal`, `exclude_categories`, `diet_tags`
- `last_recipes: list[RecipeSummary]` — результат последнего `rank`, чтобы `cook` работал без `id`
- `last_menu_plan_id: UUID | None`
- `history: deque[str]` — последние 20 команд (для `history`)

История REPL пишется в `~/.sk_cli_history` (readline-формат) для удобства между запусками.

---

## 6. Интеграция с API

### 6.1 Эндпоинты, которые CLI вызывает

Все пути относительные, контракт — `version: 1` в заголовке `X-SK-Client: cli/0.1`.

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/v1/auth/me` | Текущий пользователь |
| `GET` | `/v1/families/{family_id}/profiles` | Список профилей семьи |
| `GET` | `/v1/families/{family_id}/inventory` | Инвентарь |
| `GET` | `/v1/families/{family_id}/inventory/expiring` | Срочные |
| `GET` | `/v1/recipes?q=&limit=&offset=` | Поиск рецептов |
| `GET` | `/v1/recipes/{id}` | Карточка рецепта |
| `POST` | `/v1/recipes/rank` | Ранжирование под инвентарь (body: family_id, filters) |
| `POST` | `/v1/recipes/{id}/cook` | Подтверждение приготовления |
| `POST` | `/v1/menu/plan` | Генерация меню (body: family_id, profile_id, days) |
| `GET` | `/v1/menu/{plan_id}` | Просмотр меню |
| `GET` | `/v1/menu/{plan_id}/shopping-list` | Список покупок |

### 6.2 HTTP-клиент

- `httpx.AsyncClient` с `timeout=httpx.Timeout(SK_TIMEOUT_S)` и `limits=httpx.Limits(max_connections=10)`.
- Bearer auth через `Authorization: Bearer ${SK_API_TOKEN}`.
- Retry: до 3 попыток на `GET`, **0 retry на `POST /recipes/{id}/cook` и `/menu/plan`** (идемпотентность не гарантирована).
- Rate limit: если API вернул `429` — CLI показывает оставшееся время и предлагает повторить вручную.
- Все ответы парсятся через Pydantic (`Recipe`, `RecipeRankItem`, `MenuPlan`, `ShoppingList`) — неуспех парсинга = ошибка CLI с понятным сообщением.

### 6.3 Обработка ошибок

| HTTP | Поведение CLI |
|---|---|
| `401` | «Токен невалиден. Обновите через UI/Telegram.» — REPL предлагает `auth login` |
| `403` | «Нет доступа к ресурсу (проверьте активную семью и роль)» |
| `404` | «Ресурс не найден» |
| `409` | Конфликт состояния (например, остаток уже списан другим участником). Показать текущее состояние. |
| `422` | «API отклонил запрос из-за бизнес-правила (например, аллерген)» — показать `detail[]` |
| `429` | Rate limit — показать `Retry-After` |
| `5xx` | Retry один раз, потом предложить повторить вручную |
| Timeout | Одна повторная попытка для `GET` |

### 6.4 Защитный слой CLI

Даже если API прислал рецепт, в котором есть ингредиент из `profile.allergens`, CLI **не покажет его как кандидата** в `rank` / `menu plan`. Это второе мнение поверх бизнес-логики API.

---

## 7. Сценарии использования

### 7.1 «Что приготовить из того, что есть?»

```
$ sk repl
sk> use family 42
sk> use profile 7          # у профиля 7 аллергия на арахис
sk> set max-time 45
sk> set goal low-waste
sk> recipes rank 10

# Top 10 with match %, missing ingredients, allergen flag

sk> recipes show 318
sk> cook 318
? Подтвердить списание ингредиентов: курица 600г, лук 200г, чеснок 30г. (y/N)
> y
OK: инвентарь обновлён, остатки списаны транзакционно.
```

### 7.2 Недельное меню + список покупок

```
sk> menu plan 7
? Цель: low-waste. Бюджет: 4500. Подтвердить? (y/N)
> y
plan_id=plan_2026_08_18_a1b2

sk> menu show plan_2026_08_18_a1b2
sk> menu shopping plan_2026_08_18_a1b2
```

### 7.3 Скриптовый режим (CI / dry-run)

```
$ sk --json recipes rank --top 5 | jq '.[].id'
$ sk menu plan 7 --json --budget 4500 --goal cheap
```

---

## 8. Нефункциональные требования

- **Latency:** CLI не должен добавлять > 200 мс к сетевому вызову. Сетевой вызов — основное время.
- **Размер:** бинарник через `pipx` или `uv tool install`, без тяжёлых зависимостей. `httpx`, `typer`, `rich`, `pydantic`, `prompt_toolkit` — ок. Никаких `pandas`, `numpy`, `transformers`.
- **Кросс-платформенность:** Windows / macOS / Linux. Учёт PowerShell-окружения на Windows (бэкслеши в путях только через `pathlib`).
- **Логи:** `~/.sk_cli/logs/cli.log` с ротацией. Уровень — `WARNING` по умолчанию, `--verbose` поднимает до `INFO`.
- **Тесты:** unit на `services/` (фильтрация аллергенов, ранжирование, форматирование), integration на `client/` (mock httpx), end-to-end на ключевые сценарии.
- **Безопасность:** токен только в env, не в логах, не в истории, не в `--help` выводе. `auth logout` очищает локальный кеш (если будет). Перед `cook` и `menu plan` — обязательное подтверждение пользователем (соответствует ТЗ: «автоматическое внесение … без проверки пользователем — Won't Have»).

---

## 9. Зависимости

```
typer[all]>=0.12        # CLI-фреймворк
rich>=13.7              # таблицы, прогресс, цвета
httpx>=0.27             # HTTP-клиент с async и retry
pydantic>=2.7           # валидация ответов API
prompt_toolkit>=3.0     # REPL с автокомплитом и историей
```

Dev:

```
pytest>=8
pytest-asyncio
respx                   # мок httpx
ruff
mypy
```

---

## 10. План реализации (4 недели)

| Неделя | Результат |
|---|---|
| 1 | Скелет проекта, конфиг, HTTP-клиент, Pydantic-модели, команды `auth whoami` / `inventory list`. Unit-тесты на клиент. |
| 2 | Команды `recipes find/show/rank`, защитный фильтр аллергенов, Rich-таблицы. |
| 3 | REPL (`prompt_toolkit`), сессионное состояние, команды `cook` с подтверждением, `menu plan/show/shopping`. |
| 4 | JSON-режим, скриптовые сценарии, e2e-тесты на 5 use cases из ТЗ, документация, упаковка через `pyproject.toml` для `pipx`. |

---

## 11. Что НЕ делаем в этой итерации

- Прямой доступ к БД.
- Изменение каталога рецептов.
- Голос, OCR, штрихкоды.
- Интерактивное редактирование инвентаря (только просмотр и списание через `cook`).
- Многосемейный режим (одна сессия = одна семья; переключение — через `use family`).

---

## 12. Открытые вопросы

1. Будет ли API возвращать `match_percent` и список `missing_ingredients` в `/recipes/rank`, или CLI должен считать сам по `/inventory` + `/recipes/{id}`? **→ Договориться с backend.**
2. Где брать `SK_API_TOKEN` для CLI — отдельная долгоживущая пара логин/пароль, или short-lived токен через device-flow? **→ Определить до реализации auth.**
3. Нужен ли `--dry-run` для `cook` и `menu plan`? **→ Да, полезно для тестов.**
4. Что делать с рецептами, у которых ингредиенты есть, но в единицах, которые не сходятся с инвентарём (`г` vs `шт.`)? **→ CLI показывает предупреждение, но не блокирует.**

---

*Документ — результат проектирования CLI-агента для SmartKitchen Family на основе ТЗ `ТЗ_SmartKitchen_Family_Дмитрий_Фролов.docx` v0.1 и правил `claude.md`.*
