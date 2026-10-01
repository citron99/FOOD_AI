# Деплой на VPS (маршрут C): Docker + systemd-таймер

Готовый комплект для запуска SmartKitchen Family на Beget Cloud Server (VPS) или любом
другом сервере с root-доступом, Docker и systemd. Проект — батч-CLI: он не слушает порт,
а по расписанию пишет MD/HTML/JSON-отчёт, поэтому весь деплой сводится к «контейнер по
таймеру + каталог отчётов, который отдаёт nginx».

Сравнение маршрутов A (shared-хостинг + cron), B (GitHub Actions + FTP) и C (VPS + Docker)
и пошаговые инструкции для A и B — в [`docs/deploy_beget.md`](../../docs/deploy_beget.md).

## Когда выбирать маршрут C

- Нужен гарантированный CPU/RAM и свой интерпретатор Python (на shared-хостинге Beget
  из коробки только Python 2.7/3.6/3.7 — код проекта требует 3.11+).
- Нужны Docker-изолированные прогоны и единый образ с CI.
- В перспективе планируется API/веб-интерфейс — на VPS его есть куда поставить.
- Тарификация Beget Cloud Server почасовая, минимум — 1 час; трафик безлимитный,
  резервные копии бесплатные. Созданный и сразу удалённый сервер всё равно тарифицируется
  за полный час.

## Быстрый старт

1. В панели Beget создайте сервер и в каталоге приложений выберите **Docker** (latest) —
   Docker установится вместе с сервером, вручную ничего ставить не нужно.
2. Подключитесь и установите проект:

   ```bash
   ssh root@<IPv4>
   git clone https://github.com/citron99/FOOD_AI /root/FOOD_AI
   bash /root/FOOD_AI/deploy/vps/install.sh --with-nginx
   ```

3. Впишите секреты (файл создан с правами 600, шаблон пустой):

   ```bash
   nano /opt/food_ai/food_ai.env
   ```

4. Проверьте таймер и сделайте ручной прогон:

   ```bash
   systemctl list-timers food-ai-report.timer
   systemctl start food-ai-report.service
   journalctl -u food-ai-report.service -n 50
   tail -n 20 /opt/food_ai/cron.log
   ```

5. Откройте `http://<IPv4>/` — nginx отдаёт `index.html` из `/opt/food_ai/reports`,
   а если его там нет (обычный случай: `run-report.sh` пишет только
   `expiry_report.{md,html,json}`) — `expiry_report.html`.

Если образ в GHCR приватный, перед установкой выполните `docker login ghcr.io -u <логин>`
(токен с правом `read:packages`; храните его в `~/.docker/config.json`, не в репозитории).

## Состав каталога

| Файл | Назначение |
| --- | --- |
| `install.sh` | Одноразовая установка: каталоги, права, конфиги, systemd-юниты, nginx, контрольный прогон. Идемпотентен. |
| `run-report.sh` | Один прогон: `docker pull` → проверка прав каталога → запуск CLI → проверка результата → необязательная публикация → запись в `cron.log`. Ставится в `/usr/local/bin/food-ai-report`. |
| `food-ai-report.service` | `Type=oneshot`, `ProtectSystem=full`, `NoNewPrivileges=true`. |
| `food-ai-report.timer` | `OnCalendar=*-*-* 07:00:00 Europe/Moscow` (пояс задан в самом выражении — время прогона не зависит от `timedatectl` на сервере), `Persistent=true` (догоняющий запуск после простоя). |
| `nginx-food_ai.conf` | Отдача только `index.html` / `expiry_report.{html,md,json}` (`/` падает на `expiry_report.html`, если `index.html` нет), `autoindex off`, всё прочее — 404, заготовка под basic-auth и HTTPS. |
| `food_ai.conf.example` | Параметры прогона (без секретов) → `/opt/food_ai/food_ai.conf`, 644. |
| `food_ai.env.example` | Переменные окружения для контейнера → `/opt/food_ai/food_ai.env`, 600. |
| `publish.env.example` | Необязательная выгрузка отчёта на внешний хостинг по FTP/SFTP. |

Все рабочие копии живут в `/opt/food_ai`, а не в клоне репозитория: обновление `git pull`
не затирает ваши настройки. Повторный `install.sh` обновляет скрипт и юниты, но существующие
`food_ai.conf` и `food_ai.env` не перетирает (для перезаписи есть `--force`).

## Настройка прогона

Правьте `/opt/food_ai/food_ai.conf`:

- `IMAGE` — по умолчанию `ghcr.io/citron99/food_ai:main`. Теги `main` и короткий SHA всегда
  соответствуют коду ветки; `latest` навёрстывает `main` только после правки в
  `.github/workflows/tests.yml`, поэтому для автообновления берите `main`.
- `TIMEZONE` — часовой пояс **контейнера** (`-e TZ=...`). Без `TZ` «сегодня» считается по UTC,
  и прогон в 07:00 МСК возьмёт вчерашнюю дату (проверено, см. ниже). То же значение
  `install.sh` вписывает суффиксом в `OnCalendar` таймера, поэтому время прогона не зависит
  от `timedatectl` на сервере. Правка `TIMEZONE` в `food_ai.conf` на стоящий юнит **не**
  влияет — чтобы поменять пояс, повторите `install.sh --timezone Asia/Yekaterinburg`: он
  обновит и `OnCalendar`, и ровно одну строку `TIMEZONE=` в существующем `food_ai.conf`,
  не трогая остальных настроек и `food_ai.env` с секретами. Значение обязано быть именем IANA
  вида `Area/Location` (для UTC — `Etc/UTC`) и проверяется по `/usr/share/zoneinfo`: иначе
  установка падает сразу, не создав ничего, — мусор в `food_ai.conf` исполняется при sourcing,
  а неразобранный `OnCalendar` оставил бы сервер без таймера.
- `INVENTORY`, `PROFILE` — пути внутри контейнера. `/app/data` смонтирован read-only из
  `/opt/food_ai/data`, поэтому реальный инвентарь и профиль семьи редактируются на хосте:
  `nano /opt/food_ai/data/inventory.json`.
- `WARNING_DAYS`, `EXTRA_ARGS` — например `EXTRA_ARGS="--source iiko"` (токен — в `food_ai.env`).
- `SUGGEST_MENU="true"` включает `--suggest-menu`; при недоступном DeepSeek отчёт всё равно
  пишется, код возврата 0, а сбой виден в секции «Идеи блюд» и в `cron.log`.
  `STRICT_LLM="true"` меняет поведение на «падать с кодом 2 и не писать отчёт».

## Вариант без systemd (cron)

```cron
# /etc/cron.d/food-ai — по будням в 07:00
0 7 * * 1-5 root /usr/local/bin/food-ai-report >> /opt/food_ai/cron.log 2>&1
```

Если установка делалась с `--app-dir`, отличным от `/opt/food_ai`, добавьте переменную:

```cron
0 7 * * 1-5 root APP_DIR=/ваш/каталог /usr/local/bin/food-ai-report >> /ваш/каталог/cron.log 2>&1
```

В cron окружение урезанное, поэтому `run-report.sh` не полагается на `PATH` пользователя
и проверяет наличие `docker` явно. Часовой пояс cron — системный: сверьте `date` на сервере.

## Обновление и откат

```bash
docker pull ghcr.io/citron99/food_ai:main        # либо это сделает сам прогон
systemctl start food-ai-report.service           # немедленный пересчёт
docker image inspect -f '{{.Id}} {{.Created}}' ghcr.io/citron99/food_ai:main
```

Откат — фиксируйте образ по digest, а не по тегу:

```bash
sed -i 's|^IMAGE=.*|IMAGE="ghcr.io/citron99/food_ai@sha256:<digest>"|' /opt/food_ai/food_ai.conf
systemctl start food-ai-report.service
```

Полное удаление:

```bash
systemctl disable --now food-ai-report.timer
rm -f /etc/systemd/system/food-ai-report.{service,timer} /usr/local/bin/food-ai-report
rm -f /etc/nginx/sites-enabled/food_ai.conf && nginx -t && systemctl reload nginx
systemctl daemon-reload
# /opt/food_ai хранит отчёты и секреты — удаляйте осознанно:
# rm -rf /opt/food_ai
```

## Что проверено эмпирически (2026-09-29, образ `:main` = `sha256:60b1a5e5…`)

Репетиция выполнялась локально в Docker Desktop как стенд для VPS; ниже — факты, которые
зашиты в скрипты, а не предположения.

| Проверка | Результат |
| --- | --- |
| Пользователь внутри образа | `uid=10001(appuser) gid=999(appuser)`, `Config.User=appuser` |
| Каталог на хосте создан root'ом с 755 | `PermissionError: [Errno 13] Permission denied: 'reports/expiry_report.md'`, код возврата 1 |
| После `chown -R 10001:999 <каталог>` | код возврата 0, все три файла записаны, владелец `10001:999` |
| `TZ=Europe/Moscow` в образе | `/usr/share/zoneinfo/Europe/Moscow` на месте, `zoneinfo` работает, `date.today()` меняется (контроль: `TZ=Pacific/Kiritimati` дал следующие сутки) |
| `--env-file` с форматом `KEY=value` без `export` | принимается, значение доходит до контейнера |
| `--suggest-menu` при недоступном DeepSeek | код возврата 0, отчёт записан, в MD «Идеи блюд не получены; отчёт о сроках полный», в HTML — текст ошибки |
| `--strict-llm` при недоступном DeepSeek | код возврата 2, `cli.py: error: DeepSeek недоступен…`, каталог отчётов пуст |
| Демо-инвентарь на текущую дату | `{"expired": 3, "urgent": 0, …}` — группа `urgent` пуста, поэтому `--suggest-menu` не обращается к LLM; для теста нужен `--as-of 2026-08-17 --warning-days 3` |

### Проверка самих артефактов деплоя (2026-09-29)

Скрипты прогонялись на песочнице с заглушками `docker`/`systemctl`/`timedatectl`/`nginx`/
`chown`/`apt-get` (журнал вызовов пишется в файл, аргументы сверяются дословно),
nginx-конфиг — настоящим бинарником nginx 1.28.3 с поднятым сервером и запросами через `curl`.
Итог: 128 утверждений по скриптам и юнитам и 37 по nginx, все зелёные.

| Проверка | Результат |
| --- | --- |
| `install.sh --dry-run` | код 0, ни одного созданного файла, ни одного вызова `systemctl` |
| `install.sh` полностью | созданы conf/env/publish.env, `reports/`, `data/` (из образа), скрипт, оба юнита, сайт nginx + симлинк; `IMAGE`/`APP_DIR`/`REPORTS_DIR`/`DATA_DIR`/`TIMEZONE`/`IMAGE_UID`/`IMAGE_GID` подставлены; в `.service` подставлены `ExecStart=` и `Environment=APP_DIR=`, в `.timer` — `OnCalendar=*-*-* 07:00:00 <пояс>` |
| Повторный `install.sh` | существующие `food_ai.conf` и `food_ai.env` не перезаписаны; `--force` перезаписывает |
| `--app-dir` в зоне `ProtectHome` | для `/root/...`, `/home/...`, `/run/user/...` печатается предупреждение; для `/opt/...` и значения по умолчанию — нет |
| `--timezone` | валидное имя IANA подставляется и в `OnCalendar` таймера, и в строку `TIMEZONE=` существующего конфига (метка пользователя и `food_ai.env` целы, строка не двоится, sourcing конфига даёт то же значение); `Europe/Moscow; rm -rf /`, `"$(rm -rf /)"`, голый `UTC` и несуществующий `Foo/Bar` → код 1 с внятной причиной, ничего не создано |
| Значения директив юнитов | сверены с man-страницами systemd (первоисточник): `ProtectSystem=full`, `ProtectHome=true`, `PrivateTmp=true`, `NoNewPrivileges=true`, `IOSchedulingClass=best-effort`, `IOSchedulingPriority=0…7` (7 — низший), `Nice=-20…19`, `AccuracySec` по умолчанию `1min`; для `OnCalendar` подтверждён суффикс IANA (пример из systemd.time: `Mon *-*-* 00:00:00 Pacific/Auckland`) и то, что без суффикса выражение считается в локальном поясе хоста |
| Аргументы `docker run` | `--rm`, `-v <reports>:/app/reports`, `-e TZ=…`, `-v <data>:/app/data:ro`, `--env-file …`, образ из конфига — в этом порядке; CLI получает `--inventory/--profile/--warning-days/--out/--html-out/--json-out` |
| Секрет в argv | ключ из `food_ai.env` в аргументы `docker` **не** попадает (отдельная проверка) |
| Права каталога | при совпадении с `IMAGE_UID:IMAGE_GID` `chown` не вызывается; при расхождении — вызывается |
| Сбой контейнера | код контейнера пробрасывается наружу, сообщение уходит в `cron.log` |
| Контейнер «успешен», но файлов нет | код 1, в логе перечислены оба отсутствующих файла |
| Сбой `docker pull` | не фатален: предупреждение в журнале, работаем на локальном образе |
| Нет `food_ai.env` | предупреждение в журнале, `--env-file` в argv отсутствует |
| Сбой публикации по FTP | не фатален: код 0, локальные отчёты целы, в журнале «Публикация выполнена не полностью» |
| `cron.log` больше 1 МиБ | ротируется в `cron.log.1` |
| `nginx -t` | `syntax is ok` / `test is successful` |
| `http://<host>/` без `index.html` | 200, отдаётся `expiry_report.html` (до правки конфига здесь был 404 — дефект найден этой же проверкой) |
| `/expiry_report.md` | 200, `Content-Type: text/markdown` (без `default_type` браузер скачивал бы файл как бинарный) |
| `/expiry_report.json` | 200, `Content-Type: application/json` |
| `/food_ai.env`, `/cron.log`, `/random.txt`, `/nope`, `/sub/nested.txt`, `/reports/food_ai.env` | 404, содержимое не отдаётся |
| Сырой `/../food_ai.env` | 400 (nginx отбрасывает запрос до сопоставления `location`) |
| Заголовки | `Cache-Control: no-store`; список каталога не отдаётся |

Проверки воспроизводимы: `bash reports/vps_verify.sh` (скрипты и юниты) и
`bash reports/nginx_check.sh` (nginx-конфиг); журналы прогонов — `reports/vps_verify_out.txt`
и `reports/nginx_check_out.txt`. В репозиторий они не входят: `reports/` в `.gitignore`.

**Не выполнено:** `systemd-analyze verify` и `systemd-analyze calendar '*-*-* 07:00:00
Europe/Moscow'` — в этой сессии Docker Desktop не поднялся (`Docker Desktop is unable to
start`), а в единственном доступном дистрибутиве WSL `docker-desktop` systemd нет
(PID 1 — `/init`, бинарники `systemd-analyze`/`systemctl` отсутствуют), поэтому Linux-окружение
с systemd недоступно. Значения всех использованных директив сверены с man-страницами
systemd.exec / systemd.timer / systemd.time (см. строку таблицы выше) — это проверка по
первоисточнику, но не машинная: на сервере после установки выполните
`systemd-analyze verify /etc/systemd/system/food-ai-report.{service,timer}` и
`systemctl list-timers food-ai-report.timer` (колонка NEXT покажет ближайший запуск).

## Ограничения, о которых нужно знать

- **R:keeper (RK7) из облака не виден.** Сервер RK7 стоит в локальной сети заведения.
  «Приватная сеть» Beget объединяет только ресурсы самого Beget (VPS/облако) и каналом
  в локальную сеть ресторана не является. Нужен VPN-туннель от площадки до VPS
  (WireGuard/OpenVPN на маршрутизаторе заведения или отдельный агент), и только затем
  `RK7_BASE_URL` в `food_ai.env`. iiko Cloud, QuickResto, FrontPad, Saby, YUMA — облачные
  API, они с VPS доступны напрямую.
- **Секреты — только из переменных окружения.** Никогда не передавайте ключи флагами CLI:
  аргументы процесса видны в `ps` и попадают в журналы. `run-report.sh` использует
  `--env-file` с правами 600.
- **Отчёты по HTTP без аутентификации.** Если сервер доступен из интернета, включите
  basic-auth в `nginx-food_ai.conf` или закройте порт 80 файрволом и ходите по VPN.
- **Часовой пояс.** `OnCalendar` содержит пояс явно, а контейнеру передаётся `-e TZ=$TIMEZONE`,
  поэтому время прогона и дата в отчёте согласованы и не зависят от настроек сервера.
  Системный `timedatectl` влияет только на метки времени в `journalctl` и `cron.log`
  (для порядка: `timedatectl set-timezone Europe/Moscow` или `install.sh --set-system-tz`).
  Если вы отказались от systemd в пользу cron — там расписание считается по системному
  времени, и согласие хоста становится обязательным.
- **`ProtectHome=true` в юните.** Каталог установки должен быть вне `/home`, `/root`
  и `/run/user`, иначе сервис не сможет писать отчёты, хотя ручной запуск от root работает.
  `install.sh` предупреждает об этом при `--app-dir` в закрытой зоне; значение по умолчанию
  (`/opt/food_ai`) безопасно.
