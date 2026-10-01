# Деплой SmartKitchen Family на хостинг Beget (панель cp.sweb.ru)

Задача деплоя: раз в день строить HTML-отчёт о сроках годности и публиковать его страницей сайта.
Проект — пакетный CLI-агент (не веб-сервер): он отрабатывает, пишет файлы и завершается с кодом 0.
Значит, на хостинге нужны только три вещи — файлы, интерпретатор Python и задание cron.

Панель управления: <https://cp.sweb.ru>. Используемые разделы: **Сайты**, **Crontab**, **FTP**,
**Файловый менеджер** (Sprutio), **Помощь и поддержка**.

| Маршрут | Где считается отчёт | Нужен SSH | Нужен Python на хостинге | Деньги |
| --- | --- | --- | --- | --- |
| **A. Shared-хостинг + cron** | на вашем хостинге | да (один раз) | да, собирается 3.11 в `~/.local` | нет (уже есть тариф) |
| **B. GitHub Actions + FTP** | на раннере GitHub | нет | нет | нет |
| **C. VPS + Docker** | в контейнере на VPS | да | нет (Python внутри образа) | VPS оплачивается отдельно |

Рекомендация: **A**, если хотите, чтобы отчёт жил на самом хостинге и не зависел от GitHub;
**B**, если не хочется трогать сервер и компилировать Python; **C**, если нужен ещё и API/веб-интерфейс.

> **Beget или SpaceWeb.** Панель `cp.sweb.ru` и база знаний Beget описывают родственные площадки
> одной группы, но учётные данные у них разные. Аксиомы, которые понадобились на аккаунте
> `citron99ra` (проверено 2026-09-29 по FTP, HTTP и заголовкам сервера):
>
> | Что | Beget | SpaceWeb (наш аккаунт) |
> | --- | --- | --- |
> | Технический домен сайта | `<логин>.beget.tech` | `<логин>.<имя-сайта>.swtest.ru`, временный — `<логин>.temp.swtest.ru` |
> | Корень сайта | `~/<имя-сайта>/public_html` | `~/<имя-сайта>/public_html` (совпадает) |
> | Адрес FTP | `<логин>.beget.tech` | только IP сервера, DNS-имени у FTP нет |
> | Сертификат FTP-сервера | — | `*.sweb.ru`, то есть по IP не проверяется (см. маршрут B) |
> | Веб-сервер | nginx/apache | Apache 2.4 + PHP 7.4, `<FilesMatch>` и `Require all denied` работают |

## Что обязательно знать до начала (проверено 2026-09-29)

**1. Python 3.11+ на shared-хостинге из коробки нет.** В `pyproject.toml` заявлено
`requires-python = ">=3.11"`, а документация Beget прямо перечисляет доступные интерпретаторы:

> «На большинстве серверов в Docker-контейнере установлено несколько вариантов: `/usr/bin/python2.7`,
> `/usr/bin/python3.6`, `/usr/bin/python3.7`» — [Установка Python, база знаний Beget](https://beget.com/ru/kb/how-to/web-apps/python)

Эмпирическая проверка в контейнере `python:3.7-slim` (та самая версия, что есть на хостинге):

```text
File "/app/agent/adapters/base.py", line 32, in <module>
    Transport = Callable[[str, bytes | None, dict[str, str], float], bytes]
TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
Python 3.7.17 — EXIT=1
```

Алиас вычисляется при импорте (PEP 604 работает с 3.10), поэтому на стоковом `python3` агент
даже не импортируется. Выход — собрать Python 3.11 в `~/.local` (это же рекомендует Beget в
разделе «Локальная установка Python»), либо выбрать маршрут B/C, где Python на хостинге не нужен.

**2. POS-серверы в локальной сети заведения с хостинга недоступны.** `RK7_BASE_URL=http://192.168.0.10:8080`
и другие адреса вида `192.168.x.x`/`10.x.x.x` из интернета не маршрутизируются. На хостинге реально
работают: `--inventory data/inventory.json` (демо-данные или ваш файл) и облачные POS с публичными
endpoint'ами (iiko Cloud, QuickResto, Saby, YUMA) — при условии, что исходящий HTTPS с хостинга
разрешён (проверяется диагностикой ниже).

**3. Секреты — только переменные окружения.** Токены POS и `DEEPSEEK_API_KEY` хранятся либо в файле
`~/FOOD_AI/.env.cron` с правами `600` **вне** веб-папки (маршрут A), либо в секретах GitHub
(маршрут B), либо в `--env-file` вне публичных каталогов (маршрут C). В CLI-флагах и в репозитории
секретов быть не должно — агент читает их только из `os.environ`.

**4. Тег `latest` в реестрах был устаревшим.** Проверка реестра показала: `ghcr.io/citron99/food_ai:latest`
и `a08037/food_ai:latest` указывали на образ от `v1.0.0` (2026-09-25), а актуальный код был в тегах
`main` и `47416af` (2026-09-29). Причина — `docker/metadata-action` со вкусом `latest=auto` проставляет
`latest` только на semver-теги. В `.github/workflows/tests.yml` добавлена строка
`type=raw,value=latest,enable={{is_default_branch}}`, после следующего пуша в `main` тег `latest`
догонит код. **Пока этого пуша не было, используйте тег `main`** (или SHA-тег).

---

## Шаг 0. Диагностика аккаунта (1 минута, без SSH)

Панель → **Crontab** → вкладка **«Запустить скрипт»** → тип «Произвольная команда», вставить и запустить
(лог появится под формой):

```bash
whoami; hostname; echo HOME=$HOME; date; ls -d /usr/bin/python3*; python3 -V 2>&1; ~/.local/bin/python3.11 -V 2>&1; for t in git wget curl tar make gcc rsync; do command -v $t >/dev/null && echo "$t: есть" || echo "$t: НЕТ"; done; curl -sS -o /dev/null -w "deepseek https=%{http_code}\n" --max-time 10 https://api.deepseek.com/ 2>&1 | tail -1
```

Что покажет лог и как это использовать:

| Вывод | Что означает |
| --- | --- |
| `HOME=/home/<буква>/<логин>` | точный домашний каталог — подставляйте в команды cron |
| `/usr/bin/python3.6`, `python3.7` | стоковые версии; `python3 -V` = 3.6/3.7 → нужен шаг A2 |
| `~/.local/bin/python3.11 -V` = `Python 3.11.x` | Python уже собран, шаг A2 можно пропустить |
| `git: есть` | обновление кода через `git pull` (иначе — tar-архив с GitHub) |
| `curl … deepseek https=200/401/404` | исходящий HTTPS работает → `--suggest-menu` возможен |
| `curl: (6) Could not resolve host` | исходящего интернета нет → только JSON-инвентарь, без LLM |

В разделе **Сайты** выпишите: имя сайта, его домен и корневую директорию (обычно
`/home/<буква>/<логин>/<домен>/public_html`). Технический домен аккаунта — `<логин>.beget.tech`.

---

## Маршрут A. Shared-хостинг + cron

### A1. Включить SSH и загрузить проект

SSH-доступ включается у FTP-аккаунта: раздел **FTP** → создать/изменить аккаунт → включить SSH
(для основного аккаунта SSH включается там же либо тикетом в поддержку). Подключение двухступенчатое
(это требование окружения Beget):

```bash
ssh <логин>@<логин>.beget.tech     # вход на сервер хостинга
ssh localhost -p222                # вход в Docker-контейнер аккаунта (тот же пароль)
```

Загрузите проект в `~/FOOD_AI` файловым менеджером Sprutio или по FTP/SFTP. Нужны: `agent/`, `data/`,
`deploy/`, `pyproject.toml`, `README.md`, `tests/` (по желанию — для самопроверки на сервере).
**Не** загружайте: `.git/`, `.env`, `reports/`, `.wb/`, `__pycache__/`.

Вместо ручной загрузки можно скачать архив ветки (репозиторий публичный):

```bash
cd ~ && wget -O food_ai.tar.gz https://github.com/citron99/FOOD_AI/archive/refs/heads/main.tar.gz \
  && tar -xzf food_ai.tar.gz && mkdir -p ~/FOOD_AI \
  && cp -a FOOD_AI-main/agent FOOD_AI-main/data FOOD_AI-main/deploy FOOD_AI-main/pyproject.toml FOOD_AI-main/README.md FOOD_AI-main/tests ~/FOOD_AI/ \
  && rm -rf food_ai.tar.gz FOOD_AI-main
```

### A2. Собрать Python 3.11 (один раз)

В контейнере аккаунта (после `ssh localhost -p222`):

```bash
bash ~/FOOD_AI/deploy/beget_bootstrap.sh --project ~/FOOD_AI
```

Скрипт повторяет документированную Beget процедуру: собирает OpenSSL 1.1.1w в `~/.local`, затем
Python 3.11.13 (`--with-openssl=$HOME/.local`), проверяет модули `ssl`/`zlib`/`hashlib`, прогоняет
тесты проекта и строит пробный отчёт. Ничего вне `$HOME` не меняется, root не нужен. Ориентировочно
20–40 минут; PGO-оптимизация (`--enable-optimizations`) по умолчанию выключена, чтобы сборка не
занимала в разы больше времени (для ежедневного отчёта она ничего не даёт). Полезные флаги:
`--dry-run` (показать команды, ничего не выполняя), `--force` (пересобрать), `--optimizations`.

Проверка руками, если скрипт не использовался:

```bash
~/.local/bin/python3.11 -V                                  # Python 3.11.13
~/.local/bin/python3.11 -c "import ssl; print(ssl.OPENSSL_VERSION)"   # без ssl не будет HTTPS
cd ~/FOOD_AI && ~/.local/bin/python3.11 -m unittest discover -s tests  # 102 теста, OK
```

### A3. Каталог публикации и файл секретов

```bash
mkdir -p ~/<домен>/public_html/food_ai                 # или public_html технического домена
cd ~/FOOD_AI && umask 077 && cat > .env.cron <<'EOF'
# Секреты только здесь, файл вне веб-папки, права 600.
# export DEEPSEEK_API_KEY=...
# export IIKO_API_TOKEN=...
EOF
chmod 600 .env.cron && ls -l .env.cron
```

### A4. Задание cron

Раздел **Crontab → Мастер заданий**, тип «Произвольная команда» (подставьте свои `<буква>`, `<логин>`, `<домен>`):

```bash
. $HOME/FOOD_AI/.env.cron; cd $HOME/FOOD_AI && $HOME/.local/bin/python3.11 -m agent.cli --inventory data/inventory.json --warning-days 3 --out reports/expiry_report.md --json-out reports/expiry_report.json --html-out $HOME/<домен>/public_html/food_ai/index.html >> $HOME/FOOD_AI/cron.log 2>&1
```

Расписание: ежедневно в 07:00 → минуты `0`, часы `7`, день месяца/месяц/день недели `*`
(вкладка «Составить задание вручную»: `0 7 * * *`). Время — по часовому поясу сервера: его показал
шаг 0 (`date`). Перед сохранением нажмите кнопку проверки в мастере — панель выведет лог и статус
(«Выполнено» или код ошибки).

Вариант с идеями блюд (нужен `DEEPSEEK_API_KEY` в `.env.cron` и исходящий HTTPS):

```bash
. $HOME/FOOD_AI/.env.cron; cd $HOME/FOOD_AI && $HOME/.local/bin/python3.11 -m agent.cli --inventory data/inventory.json --warning-days 3 --suggest-menu --html-out $HOME/<домен>/public_html/food_ai/index.html >> $HOME/FOOD_AI/cron.log 2>&1
```

Сбой DeepSeek отчёт не роняет: детерминированная часть публикуется, а недоступность LLM фиксируется
в секции «Идеи блюд» и в `cron.log`. Если отчёт без идей вам не нужен — добавьте `--strict-llm`
(тогда при сбое LLM задание завершится кодом 2 и файл не перезапишется).

### A5. Проверка результата

1. `https://<домен>/food_ai/` (или `https://<логин>.beget.tech/food_ai/`) открылся, внутри — отчёт
   с сегодняшней датой и секциями «Сводка», «Просроченные продукты», «Требуется ручной учёт».
2. В `~/FOOD_AI/cron.log` — строка JSON-сводки, например
   `{"expired": 3, "urgent": 0, "normal": 1, "no_date": 1, "ignored": 1}`, и нет traceback.
3. На вкладке **Crontab → Запустить скрипт** видно последнее выполнение со статусом.

### A6. Обновление кода

```bash
cd ~/FOOD_AI && git pull                # если git есть (шаг 0) и каталог — клон репозитория
# иначе: повторить A1 (tar-архив ветки main) — данные в data/ и .env.cron не затираются,
# если копировать только agent/ и deploy/
```

---

## Маршрут B. Отчёт строится в GitHub Actions и публикуется по FTP

Python на хостинге не нужен вовсе: CI собирает HTML и загружает его в веб-папку. Секреты POS/LLM
тогда живут в GitHub (Settings → Secrets and variables → Actions), а не на хостинге.

**Этот маршрут выбран 2026-09-30.** Workflow уже лежит в репозитории:
[`.github/workflows/publish-report.yml`](../.github/workflows/publish-report.yml) — копировать его
никуда не нужно. Порядок включения:

1. Создайте отдельный FTP-аккаунт для публикации: панель → **Инструменты → FTP-аккаунты →
   Добавить FTP-аккаунт**. В поле **«папка доступа»** выберите корень сайта «ЕДА»
   (`eda_citron99_garden/public_html`) и задайте свой пароль — аккаунт получит доступ только
   к этой папке, поэтому секрет в GitHub не открывает ни панель, ни остальные файлы
   ([Работа по FTP — база знаний SpaceWeb](https://help.sweb.ru/rabota-po-ftp_92.html)).
   Заработать можно и на учётных данных основного аккаунта (его корень — домашняя папка):
   тогда задайте `FTP_DIR=eda_citron99_garden/public_html` (см. п. 2), но помните: этот пароль
   открывает всю панель управления, а секрет в GitHub не должен давать таких прав.
2. В GitHub: Settings → Secrets and variables → Actions.
   * Secrets: `FTP_HOST` — для этого аккаунта это IP `77.222.40.35`: DNS-имени у FTP нет;
     `FTP_USER` и `FTP_PASSWORD` — нового аккаунта; необязательный `DEEPSEEK_API_KEY` (нужен
     только для идей блюд).
   * Variables: `PUBLIC_URL` — `http://eda.citron99.garden.swtest.ru` (технический домен сайта
     «ЕДА», гарантированно резолвится публично; основной `http://eda.citron99.garden` заработает,
     как только развернётся DNS домена `citron99.garden`): если задан, workflow после загрузки
     скачивает файлы обратно и сверяет sha256, то есть публикация проверяет сама себя. `FTP_DIR` —
     путь от корня FTP-аккаунта до каталога отчёта: для нового аккаунта с «папкой доступа»
     оставьте пустым (заливка идёт в корень аккаунта = корень сайта), для основного — задайте
     `eda_citron99_garden/public_html`.
3. Первый запуск — вручную (Actions → publish-report → Run workflow), чтобы увидеть лог загрузки.
   Дальше он идёт по расписанию `0 4 * * *` = 07:00 МСК (cron в Actions всегда по UTC). Дату отчёта
   задаёт пояс раннера — в workflow выставлено `TZ=Europe/Moscow`.

Что именно делает workflow: собирает три файла (`expiry_report.html` публикуется как `index.html`,
рядом `expiry_report.md` и `expiry_report.json`), проверяет, что все три непусты, и выгружает их
по FTP. Идеи блюд запрашиваются флагом `--suggest-menu`, но **без** `--strict-llm`: без ключа
DeepSeek отчёт всё равно выпускается — проверено, rc=0 и предупреждение в stderr; тот же случай со
`--strict-llm` даёт rc=2 и сорвал бы ежедневный cron.

Путь загрузки проверен эмпирически 2026-09-30: черновой файл, залитый тем же curl в корень
FTP-сессии (URL без подкаталогов — ровно так будет писать аккаунт с «папкой доступа»), появился
в корне аккаунта; нормализация `FTP_DIR` (пусто / `eda_citron99_garden/public_html` / лишние
слэши) собирает корректные URL. Итоговый адрес публикации (`PUBLIC_URL`) после этого — без
хвостового слэша.

Сайт «ЕДА» создан в панели 2026-09-30 (поддомен `eda.citron99.garden` основного домена, корень
`/home/c/citron99ra/eda_citron99_garden/public_html`), отчёт за 2026-09-30 выгружен туда по FTP и
проверен по HTTP 2026-10-01: `index.html`, `expiry_report.md`, `expiry_report.json` отдают HTTP 200,
sha256 каждого совпадает с локальной сборкой (доступ по техническому домену
`eda.citron99.garden.swtest.ru` → `77.222.40.35`).

**Почему в curl стоит `--insecure`.** Хостинг поддерживает AUTH TLS (сервер отвечает 234), но
сертификат FTP-сервера выписан на `*.sweb.ru`, а аккаунт доступен только по IP, поэтому проверка
имени хоста не может пройти никогда. Проверено:

```text
$ curl --ssl-reqd -u … -T reports/expiry_report.json ftp://77.222.40.35/eda_citron99_garden/public_html/
curl: (60) schannel: SNI or certificate check failed: SEC_E_WRONG_PRINCIPAL — неверное имя субъекта
```

`--ssl-reqd --insecure` сохраняет шифрование канала (пароль не идёт открытым текстом), но сервер при
этом не удостоверяется. Строгая аутентификация возможна только по SFTP, а он требует включённого
SSH — это маршрут A.

Плюсы: ни SSH, ни компиляции, ни Python на хостинге; секреты в одном месте; публикация
самопроверяемая. Минусы: FTP-пароль хранится в секретах GitHub; schedule-запуски GitHub иногда
стартуют с задержкой в минуты; **отчёт собирается из `data/inventory.json`, лежащего в репозитории** —
LAN-POS недоступен из GitHub ровно так же, как и с shared-хостинга, поэтому свежесть отчёта
ограничена свежестью этого файла.

---

## Маршрут C. VPS/облачный сервер Beget + Docker

Готовый комплект (установщик, systemd-юниты, конфиг nginx, шаблоны конфигов, операционный
README) лежит в [`deploy/vps/`](../deploy/vps/README.md) — этот раздел описывает маршрут
в целом и объясняет, откуда взялись зашитые в скрипты значения.

### C1. Сервер

Beget Cloud Server тарифицируется **почасово, минимум — 1 час**; трафик безлимитный, резервные
копии бесплатные. Созданный и тут же удалённый сервер всё равно будет оплачен за полный час.

В каталоге приложений при создании сервера есть готовое приложение **Docker** (latest) —
Docker ставится вместе с сервером, вручную ничего устанавливать не нужно. ОС — Ubuntu/Debian
с systemd, что и предполагается комплектом в `deploy/vps/`.

### C2. Образ и два обязательных флага

Образ собирается в CI и доступен публично (pull без логина — проверено: манифест отдаётся
анонимно). Актуальный код — в теге `main` (тег `latest` догонит после следующего пуша,
см. пункт 4 выше).

Вручную, без установщика:

```bash
docker pull ghcr.io/citron99/food_ai:main        # или a08037/food_ai:main (Docker Hub)

mkdir -p /opt/food_ai/reports /opt/food_ai/data
# ОБЯЗАТЕЛЬНО: контейнер работает от appuser (UID 10001 / GID 999). Каталог, созданный
# root'ом с правами 755, ему на запись недоступен — будет PermissionError и код 1.
chown -R 10001:999 /opt/food_ai/reports

umask 077 && printf 'DEEPSEEK_API_KEY=...\n' > /opt/food_ai/food_ai.env && chmod 600 food_ai.env

docker run --rm \
  -v /opt/food_ai/reports:/app/reports \
  -e TZ=Europe/Moscow \
  --env-file /opt/food_ai/food_ai.env \
  ghcr.io/citron99/food_ai:main \
  --inventory data/inventory.json --warning-days 3 \
  --out reports/expiry_report.md --html-out reports/expiry_report.html \
  --json-out reports/expiry_report.json
```

Оба добавленных флага проверены эмпирически 2026-09-29:

- **`chown 10001:999`** — без него `[Errno 13] Permission denied: 'reports/expiry_report.md'`,
  код возврата 1; после него код 0 и все три файла принадлежат `10001:999`.
  UID/GID взяты не наугад: `docker run --rm --entrypoint id <образ> -u` печатает `10001`,
  `-g` — `999`, `Config.User=appuser`. `install.sh` определяет их из образа сам.
- **`-e TZ=Europe/Moscow`** — без `TZ` «сегодня» в контейнере считается по UTC, а `date.today()`
  и есть значение `--as-of` по умолчанию. Прогон в 07:00 МСК без `TZ` возьмёт вчерашнюю дату.
  Контроль: `TZ=Pacific/Kiritimati` дал следующие сутки — значит часовой пояс действительно
  применяется, а не игнорируется.

Проверено также: образ `ghcr.io/citron99/food_ai:main` (Python 3.12 внутри) содержит код волн
5–6 и п.17 — `agent.models`, `make_rest_source`, `RkeeperConfig.max_response_bytes=10485760`,
защита `_DOCTYPE_RE=b'<!DOCTYPE'`; запуск печатает
`{"expired": 3, "urgent": 0, "normal": 1, "no_date": 1, "ignored": 1}` и завершается кодом 0.

### C3. Регулярность

Предпочтительно systemd-таймер из комплекта: `bash deploy/vps/install.sh --with-nginx --set-system-tz`.
Он ставит `run-report.sh` в `/usr/local/bin/food-ai-report`, юниты
`food-ai-report.{service,timer}` (`OnCalendar=*-*-* 07:00:00 Europe/Moscow`, `Persistent=true` —
догоняющий запуск после простоя), сайт nginx и делает контрольный прогон. Скрипт идемпотентен и не
перезаписывает заполненные `food_ai.conf` / `food_ai.env` без `--force`.

Вариант на cron — в [`deploy/vps/README.md`](../deploy/vps/README.md#вариант-без-systemd-cron).

**Часовой пояс прогона задан в самом таймере.** `install.sh` подставляет значение `--timezone`
(по умолчанию `Europe/Moscow`) прямо в выражение `OnCalendar`, поэтому прогон случится в 07:00
по указанному поясу независимо от `timedatectl` на сервере. Основание — `systemd.time(5)`:
«Timezone can be specified as the literal string "UTC", or the local timezone, … or the timezone
in the IANA timezone database format», примеры оттуда `*-*-* 00:00:00 UTC` и
`Mon *-*-* 00:00:00 Pacific/Auckland`; «If omitted, it defaults to local». Тот же пояс передаётся
контейнеру как `-e TZ=…`, так что дата в отчёте (`--as-of` по умолчанию = `date.today()`) и время
старта согласованы.

Другой пояс: `bash deploy/vps/install.sh --timezone Asia/Yekaterinburg` — скрипт обновит
`OnCalendar` в юните и ровно одну строку `TIMEZONE=` в существующем `food_ai.conf`, остальные
настройки и `food_ai.env` не тронет. Значение обязано быть именем IANA вида `Area/Location`
(для UTC — `Etc/UTC`) и проверяется по `/usr/share/zoneinfo`; при мусоре (`UTC` без `Etc/`,
несуществующее имя, символы вроде `;` или `$( )`) установка прерывается до создания файлов:
`food_ai.conf` исполняется скриптом прогона через `.`, так что подстановка в него непроверенной
строки была бы выполнением кода.

Системный `timedatectl` после этого влияет только на метки времени в `journalctl` и `cron.log` —
приведите его к тому же поясу (`--set-system-tz` или `timedatectl set-timezone Europe/Moscow`),
чтобы журнал читался. В варианте на cron (без systemd) системный пояс по-прежнему определяет
время срабатывания задания, там совпадение обязательно.

### C4. Публикация

`deploy/vps/nginx-food_ai.conf` отдаёт из `/opt/food_ai/reports` только белые имена
(`/`, `/index.html`, `/expiry_report.{html,md,json}`), всё прочее — 404, `autoindex off`,
`Cache-Control: no-store`. Конфиг проверен настоящим nginx 1.28.3 (синтаксис и поведение
на живом сервере), подробности — в таблице проверок в `deploy/vps/README.md`.

Отчёт содержит данные о запасах заведения: если сервер торчит в интернет, включите
basic-auth (заготовка в конфиге есть) или закройте порт 80 файрволом.

### C5. R:keeper и другие POS с VPS

Это единственный маршрут, где в принципе возможна работа с R:keeper, но **не через
«приватную сеть» Beget**. По документации Beget приватная сеть «позволяет объединить
несколько Beget VPS в одну сеть с каналом в 1 Гб/сек» — то есть она связывает ресурсы
самого Beget и мостом в локальную сеть заведения не является. Адрес вида
`RK7_BASE_URL=http://192.168.0.10:8080` с облачного сервера недостижим.

Реальный вариант — VPN-туннель от площадки до VPS (WireGuard/OpenVPN на маршрутизаторе
заведения либо отдельный агент на машине в сети ресторана), и только после этого
`RK7_BASE_URL` в `food_ai.env`.

Облачные POS доступны с VPS напрямую, без туннеля: iiko Cloud, QuickResto, FrontPad,
Saby, YUMA — токены в `food_ai.env`, `EXTRA_ARGS="--source iiko"` в `food_ai.conf`.

---

## Типовые ошибки и что делать

| Симптом | Причина | Действие |
| --- | --- | --- |
| `TypeError: unsupported operand type(s) for \|` в cron.log | запуск стоковым `python3` (3.6/3.7) | использовать `$HOME/.local/bin/python3.11` (шаг A2) |
| `ModuleNotFoundError: No module named 'agent'` | cron запущен не из каталога проекта | в команде должен быть `cd $HOME/FOOD_AI &&` перед `python3.11 -m agent.cli` |
| Страница 404, но cron.log чистый | неверный путь `--html-out` или нет каталога `food_ai/` | сверить путь с разделом **Сайты** (корень `public_html`), создать каталог |
| `command not found: python3.11` | cron не видит `~/.local/bin` | указывать абсолютный путь `$HOME/.local/bin/python3.11` |
| `AdapterAuthError: не заданы переменные окружения …` | секреты не попали в окружение cron | `. $HOME/FOOD_AI/.env.cron;` в начале команды, `chmod 600`, переменные через `export` |
| Отчёт есть, но секция «Идеи блюд» сообщает о сбое | нет `DEEPSEEK_API_KEY` или нет исходящего HTTPS | это graceful degradation, отчёт корректен; при необходимости задать ключ и проверить доступ диагностикой шага 0 |
| Пустой cron.log, задание не запускается | задание выключено в разделе Crontab | включить переключателем напротив задания; проверить расписание и часовой пояс сервера |
| `PermissionError: [Errno 13] Permission denied: 'reports/…'` при запуске контейнера (маршрут C) | каталог отчётов создан root'ом, а контейнер работает от `appuser` (UID 10001 / GID 999) | `chown -R 10001:999 /opt/food_ai/reports`; `install.sh` и `run-report.sh` делают это сами |
| Отчёт считается от вчерашней/завтрашней даты (маршрут C) | контейнеру не передан `TZ`, «сегодня» берётся по UTC | `-e TZ=Europe/Moscow` в `docker run` или `TIMEZONE=` в `food_ai.conf` |
| Таймер срабатывает не в 07:00 МСК (маршрут C) | юнит поставлен старой версией `install.sh` — без пояса в `OnCalendar`, значит время считается по системному TZ сервера; либо пояс изменён только в `food_ai.conf`, а юнит не переустановлен | `bash deploy/vps/install.sh --timezone Europe/Moscow` (перепишет `OnCalendar` и строку `TIMEZONE=`); проверить `systemctl cat food-ai-report.timer` и `systemctl list-timers food-ai-report.timer` |
| `install.sh` падает с `--timezone: «…» отсутствует в базе IANA` / `не похоже на имя IANA` | передано имя не вида `Area/Location` (например голый `UTC`) или несуществующее | правильное имя для UTC — `Etc/UTC`, список — `timedatectl list-timezones`; установка прерывается до создания файлов, так что ничего чинить не нужно |
| `http://<IPv4>/` отдаёт 404, хотя файлы в `/opt/food_ai/reports` есть (маршрут C) | в конфиге nginx `location = /` искал только `index.html`, которого `run-report.sh` не пишет | обновить `deploy/vps/nginx-food_ai.conf` (там `try_files /index.html /expiry_report.html =404`) и `systemctl reload nginx` |
| Сервис падает с «Permission denied», хотя ручной запуск от root работает (маршрут C) | `--app-dir` указан в `/home`, `/root` или `/run/user`, а в юните `ProtectHome=true` | ставить в `/opt` либо убрать `ProtectHome` из `food-ai-report.service` |
| Файлы по FTP «загружены», но на сайте их нет (маршрут B) | у аккаунта задана «папка доступа», а `FTP_DIR` заполнен — заливка ушла внутрь корня сайта (`…/public_html/eda_citron99_garden/public_html/…`) | для аккаунта с «папкой доступа» `FTP_DIR` должен быть пустым; заполнять его только для аккаунта, который видит всю домашнюю папку |

---

## Источники

- [Установка Python — база знаний Beget](https://beget.com/ru/kb/how-to/web-apps/python) (версии на сервере, локальная сборка Python 3.10+ с OpenSSL)
- [Общие сведения по установке приложений (виртуальное окружение Docker)](https://beget.com/ru/kb/how-to/web-apps/obshhie-svedeniya-po-ustanovke-prilozhenij-virtualnoe-okruzhenie-docker) (SSH → `ssh localhost -p222`, POSIX ACL на `~/.local`)
- [Настройка планировщика Cron](https://beget.com/ru/kb/manual/crontab) (Мастер заданий, ручная настройка, «Запустить скрипт»)
- [FTP-аккаунты](https://beget.com/ru/kb/manual/ftp) (вход по учётным данным панели, дополнительные аккаунты с доступом к одной папке, SFTP требует включённого SSH)
- [Работа по FTP — база знаний SpaceWeb](https://help.sweb.ru/rabota-po-ftp_92.html) («Инструменты → FTP-аккаунты → Добавить FTP-аккаунт»; у аккаунта есть «папка доступа», ограничивающая его одной директорией; SSH для FTP-пользователя не включается, SFTP настраивается отдельно)
- [Виртуальные серверы (VPS/VDS)](https://beget.com/ru/kb/manual/virtual-servers) — дословно: «минимальная единица тарификации – один час. Если услуга работала меньше часа, тарифицируется полный час работы», «Сервер, созданный и сразу удаленный, также тарифицируется как один полный час», «неограниченный трафик», «автоматические бэкапы», приложения из маркетплейса «(например, WordPress, Docker)», и «Приватная сеть позволяет объединить несколько VPS в одну сеть с каналом в 1 Гб/сек» — то есть только ресурсы Beget между собой, а не мост в локальную сеть заведения
- [Установка Docker на VPS — каталог приложений](https://beget.com/ru/cloud/marketplace/docker) (готовое приложение Docker с пресетом 2 ядра / 2 ГБ / 30 ГБ) и [каталог приложений](https://beget.com/ru/cloud/marketplace) в целом
