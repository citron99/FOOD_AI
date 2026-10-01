#!/usr/bin/env bash
# SmartKitchen Family — установка на VPS (маршрут C: Beget Cloud Server + Docker).
#
# Запускается один раз от root:
#   ssh root@<IPv4>
#   git clone https://github.com/citron99/FOOD_AI /root/FOOD_AI
#   bash /root/FOOD_AI/deploy/vps/install.sh
#
# Скрипт идемпотентен: повторный запуск обновит конфиги-примеры и юниты,
# но НЕ перетрёт заполненные food_ai.conf / food_ai.env (только с --force).
# Секретов в репозитории нет: food_ai.env создаётся пустым шаблоном с правами 600,
# ключи вписывает администратор.
#
#   --app-dir DIR      корень установки (по умолчанию /opt/food_ai)
#   --image REF        образ из реестра (по умолчанию ghcr.io/citron99/food_ai:main)
#   --timezone TZ      часовой пояс контейнера и таймера, имя IANA вида Area/Location
#                      (по умолчанию Europe/Moscow; для UTC — Etc/UTC)
#   --set-system-tz    дополнительно выполнить timedatectl set-timezone TZ
#   --with-nginx       установить nginx и конфиг сайта (отчёты по HTTP)
#   --no-enable        не включать таймер (только установить файлы)
#   --force            перезаписать существующие food_ai.conf / food_ai.env
#   --dry-run          печатать команды, ничего не меняя
#   -h, --help         эта справка
set -uo pipefail

APP_DIR="/opt/food_ai"
IMAGE="ghcr.io/citron99/food_ai:main"
TIMEZONE="Europe/Moscow"
# --timezone задан явно? Тогда строка TIMEZONE обновляется даже в существующем
# food_ai.conf — иначе пояс контейнера разъедется с поясом в OnCalendar таймера.
TZ_EXPLICIT="false"
SET_SYSTEM_TZ="false"
WITH_NGINX="false"
ENABLE_TIMER="true"
FORCE="false"
DRY_RUN="false"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Пути установки переопределяются через окружение: так скрипт можно прогнать
# в песочнице и так же поставить юниты в /usr/lib/systemd/system, если дистрибутив
# этого требует.
SERVICE_BIN="${SERVICE_BIN:-/usr/local/bin/food-ai-report}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
NGINX_SITES_AVAILABLE="${NGINX_SITES_AVAILABLE:-/etc/nginx/sites-available}"
NGINX_SITES_ENABLED="${NGINX_SITES_ENABLED:-/etc/nginx/sites-enabled}"
SERVICE_NAME="food-ai-report"

usage() {
    awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
}

die() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

run() {
    if [ "$DRY_RUN" = "true" ]; then
        printf '[dry-run] %s\n' "$*"
        return 0
    fi
    "$@"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --app-dir)        APP_DIR="${2:?--app-dir требует значение}"; shift 2 ;;
        --image)          IMAGE="${2:?--image требует значение}"; shift 2 ;;
        --timezone)       TIMEZONE="${2:?--timezone требует значение}"; TZ_EXPLICIT="true"; shift 2 ;;
        --set-system-tz)  SET_SYSTEM_TZ="true"; shift ;;
        --with-nginx)     WITH_NGINX="true"; shift ;;
        --no-enable)      ENABLE_TIMER="false"; shift ;;
        --force)          FORCE="true"; shift ;;
        --dry-run)        DRY_RUN="true"; shift ;;
        -h|--help)        usage; exit 0 ;;
        *)                die "неизвестный аргумент: $1 (см. --help)" ;;
    esac
done

REPORTS_DIR="$APP_DIR/reports"
DATA_DIR="$APP_DIR/data"
CONF_FILE="$APP_DIR/food_ai.conf"
ENV_FILE="$APP_DIR/food_ai.env"
PUBLISH_FILE="$APP_DIR/publish.env"

# ProtectHome=true в юните закрывает доступ к /home, /root и /run/user.
# Если каталог установки там, сервис упадёт с «Permission denied», хотя ручной
# запуск от root будет работать — предупреждаем сразу, а не после первого сбоя.
case "$APP_DIR" in
    /home/*|/root/*|/run/user/*)
        echo "ВНИМАНИЕ: --app-dir $APP_DIR лежит в зоне, закрытой ProtectHome=true"
        echo "          в $SERVICE_NAME.service. Сервис не сможет туда писать."
        echo "          Либо ставьте в /opt (по умолчанию), либо уберите ProtectHome из юнита."
        ;;
esac

# ---------- Проверка --timezone ----------
# Значение уходит в два места: строка TIMEZONE в food_ai.conf (его run-report.sh читает
# sourcing'ом) и суффикс в OnCalendar таймера. systemd.time(5) допускает в календарном
# выражении суффикс IANA («Mon *-*-* 00:00:00 Pacific/Auckland»); без суффикса выражение
# считается в системном часовом поясе хоста («If omitted, it defaults to local»).
# Мусорное значение означало бы либо неразбираемый юнит, либо исполняемый текст в конфиге,
# поэтому проверяем жёстко и падаем сразу.
case "$TIMEZONE" in
    *[!A-Za-z0-9_+/-]*) die "--timezone: «$TIMEZONE» содержит недопустимые символы (разрешены буквы, цифры, '_', '+', '-', '/')" ;;
    */*/*)              die "--timezone: «$TIMEZONE» содержит больше одного '/'" ;;
    ?*/?*)              ;;
    *)                  die "--timezone: «$TIMEZONE» не похоже на имя IANA вида Area/Location (для UTC используйте Etc/UTC)" ;;
esac
# ZONEINFO_DIR переопределяется окружением — так же, как SYSTEMD_DIR и SERVICE_BIN,
# чтобы проверку можно было прогнать в песочнице.
ZONEINFO_DIR="${ZONEINFO_DIR:-/usr/share/zoneinfo}"
if [ -d "$ZONEINFO_DIR" ]; then
    [ -e "$ZONEINFO_DIR/$TIMEZONE" ] \
        || die "--timezone: «$TIMEZONE» отсутствует в базе IANA ($ZONEINFO_DIR)"
else
    echo "ВНИМАНИЕ: $ZONEINFO_DIR не найден — имя часового пояса проверено только по формату"
fi

# ---------- 0. Предусловия ----------
if [ "$DRY_RUN" = "false" ]; then
    [ "$(id -u)" -eq 0 ] || die "нужен root (установка пишет в /etc/systemd/system и /usr/local/bin)"
    command -v docker >/dev/null 2>&1 \
        || die "docker не установлен. На Beget создайте сервер с приложением «Docker» из каталога приложений либо выполните установку вручную."
    command -v systemctl >/dev/null 2>&1 \
        || die "systemctl не найден — скрипт рассчитан на systemd (Ubuntu/Debian на Beget VPS)"
    docker info >/dev/null 2>&1 || die "демон docker не отвечает (systemctl start docker)"
fi

echo "== SmartKitchen Family: установка на VPS =="
echo "Каталог:   $APP_DIR"
echo "Образ:     $IMAGE"
echo "Часовой пояс контейнера: $TIMEZONE"
[ "$DRY_RUN" = "true" ] && echo "Режим:     --dry-run (ничего не меняем)"
echo

# ---------- 1. Образ: определяем UID/GID контейнерного пользователя ----------
# Именно под этим UID контейнер пишет отчёты в bind-mount; каталог на хосте
# должен принадлежать ему, иначе получим PermissionError.
IMAGE_UID=10001
IMAGE_GID=999
if [ "$DRY_RUN" = "false" ]; then
    echo "-- Тяну образ и определяю UID/GID пользователя внутри --"
    docker pull "$IMAGE" >/dev/null 2>&1 \
        || echo "ВНИМАНИЕ: docker pull не удался, пробую локальную копию"
    docker image inspect "$IMAGE" >/dev/null 2>&1 || die "образ $IMAGE недоступен"
    uid_from_image="$(docker run --rm --entrypoint id "$IMAGE" -u 2>/dev/null || true)"
    gid_from_image="$(docker run --rm --entrypoint id "$IMAGE" -g 2>/dev/null || true)"
    case "$uid_from_image" in (''|*[!0-9]*) ;; (*) IMAGE_UID="$uid_from_image" ;; esac
    case "$gid_from_image" in (''|*[!0-9]*) ;; (*) IMAGE_GID="$gid_from_image" ;; esac
    echo "IMAGE_UID=$IMAGE_UID IMAGE_GID=$IMAGE_GID (image_user=$(docker image inspect -f '{{.Config.User}}' "$IMAGE" 2>/dev/null))"
fi

# ---------- 2. Каталоги и права ----------
echo
echo "-- Каталоги --"
run mkdir -p "$APP_DIR" "$REPORTS_DIR" "$DATA_DIR"
if [ "$DRY_RUN" = "false" ]; then
    chmod 0755 "$APP_DIR" "$REPORTS_DIR"
    chown "$IMAGE_UID:$IMAGE_GID" "$REPORTS_DIR"
fi

# Демо-инвентарь и профиль семьи — из образа, чтобы первый прогон был рабочим.
if [ -z "$(ls -A "$DATA_DIR" 2>/dev/null)" ] || [ "$DRY_RUN" = "true" ]; then
    echo "-- Копирую data/ из образа в $DATA_DIR (демо-инвентарь и профиль) --"
    if [ "$DRY_RUN" = "false" ]; then
        cid="$(docker create "$IMAGE")"
        docker cp "$cid:/app/data/." "$DATA_DIR/" || echo "ВНИМАНИЕ: docker cp не удался"
        docker rm "$cid" >/dev/null
        chmod 0755 "$DATA_DIR"
        find "$DATA_DIR" -type f -exec chmod 0644 {} +
    else
        printf '[dry-run] docker create %s && docker cp <cid>:/app/data/. %s/\n' "$IMAGE" "$DATA_DIR"
    fi
fi

# ---------- 3. Конфигурация ----------
echo
echo "-- Конфигурация --"
# Подставляем реальные пути/образ/UID в шаблон. Функция принимает всё аргументами,
# чтобы работать и через run() в режиме --dry-run.
write_conf() {
    local src="$1" dst="$2" image="$3" app_dir="$4" reports_dir="$5" data_dir="$6" tz="$7" uid="$8" gid="$9"
    sed -e "s|^IMAGE=.*|IMAGE=\"$image\"|" \
        -e "s|^APP_DIR=.*|APP_DIR=\"$app_dir\"|" \
        -e "s|^REPORTS_DIR=.*|REPORTS_DIR=\"$reports_dir\"|" \
        -e "s|^DATA_DIR=.*|DATA_DIR=\"$data_dir\"|" \
        -e "s|^TIMEZONE=.*|TIMEZONE=\"$tz\"|" \
        -e "s|^IMAGE_UID=.*|IMAGE_UID=\"$uid\"|" \
        -e "s|^IMAGE_GID=.*|IMAGE_GID=\"$gid\"|" \
        "$src" >"$dst"
}
if [ ! -f "$CONF_FILE" ] || [ "$FORCE" = "true" ]; then
    run write_conf "$SCRIPT_DIR/food_ai.conf.example" "$CONF_FILE" \
        "$IMAGE" "$APP_DIR" "$REPORTS_DIR" "$DATA_DIR" "$TIMEZONE" "$IMAGE_UID" "$IMAGE_GID"
    run chmod 0644 "$CONF_FILE"
    echo "Создан $CONF_FILE (644)"
else
    echo "$CONF_FILE уже есть — не трогаю (перезапись: --force)"
    # Исключение: --timezone передан в командной строке явно. Обновляем только эту строку,
    # чтобы пояс контейнера (-e TZ) совпадал с поясом в OnCalendar таймера (шаг 5).
    # Остальные настройки и все секреты в food_ai.env остаются нетронутыми.
    if [ "$TZ_EXPLICIT" = "true" ]; then
        if [ "$DRY_RUN" = "false" ]; then
            sed -i "s|^TIMEZONE=.*|TIMEZONE=\"$TIMEZONE\"|" "$CONF_FILE"
            echo "  в существующем $CONF_FILE обновлена только строка TIMEZONE=\"$TIMEZONE\""
        else
            printf '[dry-run] sed -i TIMEZONE="%s" %s\n' "$TIMEZONE" "$CONF_FILE"
        fi
    fi
fi

if [ ! -f "$ENV_FILE" ] || [ "$FORCE" = "true" ]; then
    run cp "$SCRIPT_DIR/food_ai.env.example" "$ENV_FILE"
    run chmod 0600 "$ENV_FILE"
    echo "Создан $ENV_FILE (600) — ВПИШИТЕ СЕКРЕТЫ ВРУЧНУЮ"
else
    echo "$ENV_FILE уже есть — не трогаю (перезапись: --force)"
    [ "$DRY_RUN" = "false" ] && chmod 0600 "$ENV_FILE" 2>/dev/null
fi

if [ ! -f "$PUBLISH_FILE" ]; then
    run cp "$SCRIPT_DIR/publish.env.example" "$PUBLISH_FILE"
    run chmod 0600 "$PUBLISH_FILE"
fi

# ---------- 4. Рабочий скрипт ----------
echo
echo "-- Скрипт прогона --"
run install -m 0755 "$SCRIPT_DIR/run-report.sh" "$SERVICE_BIN"
echo "Установлен $SERVICE_BIN"

# ---------- 5. systemd-юниты ----------
echo
echo "-- systemd --"
run install -m 0644 "$SCRIPT_DIR/$SERVICE_NAME.service" "$SYSTEMD_DIR/$SERVICE_NAME.service"
run install -m 0644 "$SCRIPT_DIR/$SERVICE_NAME.timer" "$SYSTEMD_DIR/$SERVICE_NAME.timer"
# Юнит должен знать реальный каталог установки и путь к скрипту (см. --app-dir).
if [ "$DRY_RUN" = "false" ]; then
    sed -i -e "s|^ExecStart=.*|ExecStart=$SERVICE_BIN|" \
           -e "s|^Environment=APP_DIR=.*|Environment=APP_DIR=$APP_DIR|" \
           "$SYSTEMD_DIR/$SERVICE_NAME.service"
else
    printf '[dry-run] sed -i ExecStart=%s Environment=APP_DIR=%s %s/%s.service\n' \
        "$SERVICE_BIN" "$APP_DIR" "$SYSTEMD_DIR" "$SERVICE_NAME"
fi

# Часовой пояс прямо в OnCalendar: суффикс IANA делает время прогона независимым от
# timedatectl на сервере. Значение уже проверено (см. «Проверка --timezone» выше).
if [ "$DRY_RUN" = "false" ]; then
    sed -i "s|^OnCalendar=.*|OnCalendar=*-*-* 07:00:00 $TIMEZONE|" \
        "$SYSTEMD_DIR/$SERVICE_NAME.timer"
    echo "OnCalendar=*-*-* 07:00:00 $TIMEZONE (часовой пояс задан в самом таймере)"
else
    printf '[dry-run] sed -i OnCalendar=*-*-* 07:00:00 %s %s/%s.timer\n' \
        "$TIMEZONE" "$SYSTEMD_DIR" "$SERVICE_NAME"
fi
run systemctl daemon-reload
if [ "$ENABLE_TIMER" = "true" ]; then
    run systemctl enable --now "$SERVICE_NAME.timer"
else
    run systemctl enable "$SERVICE_NAME.timer"
fi

# ---------- 6. Часовой пояс хоста ----------
# На время прогона системный TZ больше не влияет: он зашит в OnCalendar (шаг 5).
# Совпадение нужно для читаемых меток времени в journalctl и cron.log.
echo
echo "-- Часовой пояс хоста --"
if command -v timedatectl >/dev/null 2>&1; then
    host_tz="$(timedatectl show -p Timezone --value 2>/dev/null || echo '?')"
    echo "Системный часовой пояс: $host_tz; для контейнера и таймера задан: $TIMEZONE"
    if [ "$SET_SYSTEM_TZ" = "true" ]; then
        run timedatectl set-timezone "$TIMEZONE"
    elif [ "$host_tz" != "$TIMEZONE" ]; then
        echo "Расхождение с системным часовым поясом: $host_tz ≠ $TIMEZONE."
        echo "На время прогона не влияет (OnCalendar содержит $TIMEZONE);"
        echo "совпадение нужно только для меток времени в journalctl и cron.log:"
        echo "timedatectl set-timezone $TIMEZONE  (или install.sh --set-system-tz)"
    fi
else
    echo "timedatectl не найден — проверьте часовой пояс хоста вручную (date)"
fi

# ---------- 7. nginx (необязательно) ----------
if [ "$WITH_NGINX" = "true" ]; then
    echo
    echo "-- nginx --"
    if command -v nginx >/dev/null 2>&1; then
        echo "nginx уже установлен"
    else
        run apt-get update -y
        run apt-get install -y nginx
    fi
    run install -m 0644 "$SCRIPT_DIR/nginx-food_ai.conf" "$NGINX_SITES_AVAILABLE/food_ai.conf"
    run ln -sfn "$NGINX_SITES_AVAILABLE/food_ai.conf" "$NGINX_SITES_ENABLED/food_ai.conf"
    run nginx -t
    run systemctl reload nginx
fi

# ---------- 8. Контрольный прогон ----------
echo
echo "-- Контрольный прогон --"
if [ "$DRY_RUN" = "false" ]; then
    if APP_DIR="$APP_DIR" "$SERVICE_BIN"; then
        echo "OK: отчёт записан в $REPORTS_DIR"
        ls -l "$REPORTS_DIR" | sed 's/^/    /'
    else
        rc=$?
        echo "ВНИМАНИЕ: контрольный прогон завершился с кодом $rc — смотрите $APP_DIR/cron.log"
    fi
    echo
    echo "Дальше:"
    echo "  1) nano $ENV_FILE          # вписать DEEPSEEK_API_KEY / токены POS"
    echo "  2) systemctl list-timers $SERVICE_NAME.timer"
    echo "  3) systemctl start $SERVICE_NAME.service   # ручной прогон"
    echo "  4) journalctl -u $SERVICE_NAME.service -n 50"
else
    printf '[dry-run] %s\n' "$SERVICE_BIN"
fi
