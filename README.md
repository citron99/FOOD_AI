# AI_Food — SmartKitchen Family CLI Agent

[![tests](https://github.com/citron99/FOOD_AI/actions/workflows/tests.yml/badge.svg)](https://github.com/citron99/FOOD_AI/actions/workflows/tests.yml)

Это переносимый прототип проектного агента для SmartKitchen Family. Первая реализованная мини-задача — детерминированная проверка сроков годности продуктов и генерация Markdown/JSON-отчёта.

## Запуск

Из каталога проекта выполните:

```bash
python -m agent.cli --inventory data/inventory.json --as-of 2026-08-18 --warning-days 3 --out reports/expiry_report.md --json-out reports/expiry_report.json
```

Если используется Windows PowerShell:

```powershell
py -m agent.cli --inventory data/inventory.json --as-of 2026-08-18 --warning-days 3 --out reports/expiry_report.md --json-out reports/expiry_report.json
```

## Проверка

Тесты написаны на `unittest` и не требуют установки сторонних пакетов:

```bash
python -m unittest discover -s tests -v
```

Агент работает без ключей внешних сервисов. Он не переносит OCR/LLM-данные в инвентарь, не выполняет покупку и не заменяет медицинские рекомендации. Текущая версия получает уже подтверждённый JSON-инвентарь и формирует отчёт; подключение к БД должно выполняться следующим адаптером после передачи исходного проекта.

## Деплой на хостинг (Beget)

Проект работает на shared-хостинге Beget без установки зависимостей — нужен только Python 3 (предустановлен на тарифах). Пример рабочего развёртывания: проект лежит в домашней директории `~/FOOD_AI`, отчёт публикуется как статическая страница `https://<логин>.beget.tech/food_ai/`.

1. Загрузите каталог проекта в домашнюю директорию хостинга (`/home/<логин>/FOOD_AI`) через файловый менеджер (Sprutio) или FTP. Веб-папка сайта — `/home/<логин>/<логин>.beget.tech/public_html`.
2. Создайте в веб-папке каталог `food_ai/` — туда будет писаться HTML-отчёт.
3. В панели Beget откройте раздел **Crontab → Мастер заданий**, тип «Произвольная команда», и добавьте задание (замените `<логин>` на свой логин Beget):

   ```bash
   cd /home/<логин>/FOOD_AI && python3 -m agent.cli --inventory data/inventory.json --warning-days 3 --out reports/expiry_report.md --html-out /home/<логин>/<логин>.beget.tech/public_html/food_ai/index.html >> /home/<логин>/FOOD_AI/cron.log 2>&1
   ```

4. Расписание: ежедневно в 07:00 (минута `0`, час `7`, дни/месяцы/дни недели — `*`).
5. Лог выполнения — в файле `~/FOOD_AI/cron.log` (виден в файловом менеджере). Если страница не обновляется, смотрите его первым делом: там будут версия Python и traceback.

Особенности:

- Флаг `--suggest-menu` в cron не используйте — на сервере нет `DEEPSEEK_API_KEY`, агент упадёт с ошибкой авторизации. Отчёт со сроками годности DeepSeek не требует.
- Демо-инвентарь `data/inventory.json` сам не меняется: каждый день обновляется дата расчёта и статусы сроков годности, но состав продуктов тот же. Для реальных данных подключите POS-источник (`--source`, см. ниже) и задайте токены через переменные окружения в задании cron (`VAR=value; export VAR;` перед основной командой) — либо расширяйте `data/inventory.json` своим скриптом.
- Дата расчёта — текущая дата сервера; зафиксировать её можно флагом `--as-of ГГГГ-ММ-ДД`.

## Docker

Образ собирается из `Dockerfile` — зависимостей нет, используется только стандартная библиотека Python (база `python:3.12-slim`, непривилегированный пользователь):

```bash
docker build -t food_ai .
docker run --rm food_ai                          # отчёт по демо-инвентарю на текущую дату
docker run --rm -v "$PWD/reports:/app/reports" food_ai --html-out reports/expiry_report.html
```

Полезные варианты:

```bash
# отчёт на фиксированную дату (детерминированная сверка с CI)
docker run --rm food_ai --as-of 2026-08-18 --warning-days 3

# внешний источник: токены передаются через -e, секреты в образ не попадают
docker run --rm -e IIKO_API_TOKEN=... food_ai --source iiko --warning-days 3
```

Отчёты сохраняются в контейнере в `/app/reports` — монтируйте его как том, чтобы забрать файлы наружу.

## Структура

```text
AI_Food/
├── agent/cli.py                 # deterministic CLI agent
├── agent/adapters/              # интеграции с POS/учётными системами
│   ├── base.py                  # общий базовый класс REST-адаптеров
│   ├── registry.py              # реестр источников (--source)
│   ├── rkeeper.py               # R:keeper (UCS), XML-интерфейс RK7 API
│   ├── iiko.py                  # iiko, iikoCloud API /api/1/nomenclature
│   ├── quickresto.py            # QuickResto, REST API /api/products
│   ├── frontpad.py              # FrontPad, API со secret-ключом
│   ├── saby.py                  # Saby (СБИС) Presto, конфигурируемая заготовка
│   ├── yuma.py                  # YUMA, конфигурируемая заготовка
│   └── deepseek.py              # DeepSeek API (LLM-подсказки блюд)
├── data/inventory.json          # demonstration input
├── reports/                     # generated and analytical reports
├── tests/                       # unittest-тесты (без сети, без внешних ключей)
└── README.md
```

### Источники данных (--source)

По умолчанию агент читает локальный JSON (`--source json`, файл задаётся через `--inventory`). Дополнительно можно строить отчёт напрямую из справочника внешней системы:

```bash
python -m agent.cli --source iiko --as-of 2026-08-18
```

Секреты задаются только через переменные окружения:

| Источник | Переменные окружения | Примечание |
|---|---|---|
| `rkeeper` | `RK7_BASE_URL`, `RK7_STATION`, `RK7_USER`, `RK7_PASSWORD` | XML-интерфейс RK7, формат запроса сверить с версией R:keeper |
| `iiko` | `IIKO_BASE_URL` (по умолчанию `https://api-ru.iiko.services`), `IIKO_API_TOKEN` | iikoCloud API, номенклатура |
| `quickresto` | `QR_BASE_URL`, `QR_USER`, `QR_PASSWORD` | HTTP Basic, `/api/products` |
| `frontpad` | `FRONTPAD_BASE_URL`, `FRONTPAD_SECRET` | команда и поля — по документации FrontPad |
| `saby` | `SABY_BASE_URL`, `SABY_API_TOKEN`, опционально `SABY_ENDPOINT`, `SABY_NAME_FIELD`, `SABY_UNIT_FIELD` | endpoint сверить с документацией Saby |
| `yuma` | `YUMA_BASE_URL`, `YUMA_API_TOKEN`, опционально `YUMA_ENDPOINT`, `YUMA_NAME_FIELD`, `YUMA_UNIT_FIELD` | endpoint сверить с документацией YUMA |

Общие ограничения всех адаптеров: только чтение справочника (запись во внешние системы не выполняется); остатки и сроки годности не импортируются — позиции получают `quantity = 0` и попадают в «Вне активного учёта» до ручного подтверждения.

> Poster в список не включён: разработчик прекратил работу на территории РФ.

### LLM-подсказки (DeepSeek)

Флаг `--suggest-menu` добавляет в отчёт идеи блюд из продуктов с приближающимся сроком годности:

```bash
python -m agent.cli --as-of 2026-08-18 --suggest-menu
```

Нужен `DEEPSEEK_API_KEY` из кабинета [DeepSeek](https://platform.deepseek.com/) (раздел API keys). По ТЗ LLM-результат не применяется автоматически: секция в отчёте помечена как неподтверждённая подсказка, состав и аллергены проверяются вручную. Детерминированная логика отчёта от DeepSeek не зависит.

**Hard-filter аллергенов.** Поверх LLM-подсказок работает детерминированный фильтр (`agent/allergens.py`), как в cli_agent_design.md: блюда, содержащие аллергены из профиля семьи, не показываются вовсе. Профиль — JSON (`--profile`, по умолчанию `data/family_profile.json`):

```json
{"family_id": "demo", "allergens": ["орехи", "мёд"]}
```

Фильтр сравнивает слова по основе (первые 4 символа), поэтому «орехи» ловит и «орехами»; при сомнении блюдо скрывается. Число скрытых блюд фиксируется в отчёте.

### Настройка подключений (.env)

Готовый шаблон всех переменных — в [.env.example](.env.example): скопируйте его в `.env`, заполните значения и не коммойтьте `.env` в репозиторий. Как получить учётные данные:

- **R:keeper** — в менеджерской RK7 (Service → Интерфейсы) создайте интерфейс и учётную запись с правом чтения справочников; `RK7_BASE_URL` — адрес сервера в локальной сети заведения.
- **iiko** — бэк-офис iiko → Настройки → Интеграции → API keys; ключ привязывается к организациям, выдайте доступ только на чтение номенклатуры.
- **QuickResto** — учётная запись с разрешённым доступом к API (Настройки → Пользователи); `QR_BASE_URL` — адрес вашего аккаунта из личного кабинета.
- **FrontPad** — секретный ключ в личном кабинете (Настройки → API).
- **Saby / YUMA** — запросите параметры API у вендора; заготовки читают endpoint и имена полей из переменных окружения, значения по умолчанию в файле могут не совпасть с вашей конфигурацией.

Загрузку `.env` в окружение выполняет ваш shell или средство вроде `set -a; . ./.env; set +a` — сам агент файлы `.env` не читает.
