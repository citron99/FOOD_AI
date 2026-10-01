#!/usr/bin/env bash
# SmartKitchen Family — один прогон отчёта в контейнере на VPS.
#
# Вызывается systemd-таймером food-ai-report.timer (или cron, см. README.md).
# Конфигурация читается из /opt/food_ai/food_ai.conf (shell-формат, без секретов),
# секреты — только из /opt/food_ai/food_ai.env (chmod 600) и передаются контейнеру
# через --env-file. Никакие ключи не попадают в аргументы командной строки.
#
# Коды возврата: 0 — отчёт записан; 1 — ошибка запуска; код контейнера — если CLI упал.
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/food_ai}"
CONF="${CONF:-$APP_DIR/food_ai.conf}"
LOG_FILE="${LOG_FILE:-$APP_DIR/cron.log}"
LOG_MAX_BYTES="${LOG_MAX_BYTES:-1048576}"   # 1 МиБ, дальше ротируем в cron.log.1

die() { log "ОШИБКА: $*"; exit 1; }

log() {
    local line
    line="$(date '+%Y-%m-%d %H:%M:%S') $*"
    printf '%s\n' "$line"
    if [ -n "$LOG_FILE" ]; then
        printf '%s\n' "$line" >>"$LOG_FILE" 2>/dev/null || true
    fi
}

rotate_log() {
    [ -n "$LOG_FILE" ] || return 0
    [ -f "$LOG_FILE" ] || return 0
    local size
    size=$(wc -c <"$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$size" -ge "$LOG_MAX_BYTES" ]; then
        mv -f "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null || true
    fi
}

# ---------- 1. Конфигурация ----------
[ -f "$CONF" ] || die "нет файла конфигурации $CONF (запустите install.sh)"
# shellcheck source=/dev/null
. "$CONF"

IMAGE="${IMAGE:-ghcr.io/citron99/food_ai:main}"
REPORTS_DIR="${REPORTS_DIR:-$APP_DIR/reports}"
DATA_DIR="${DATA_DIR:-$APP_DIR/data}"
TIMEZONE="${TIMEZONE:-Europe/Moscow}"
INVENTORY="${INVENTORY:-data/inventory.json}"
PROFILE="${PROFILE:-data/family_profile.json}"
WARNING_DAYS="${WARNING_DAYS:-3}"
EXTRA_ARGS="${EXTRA_ARGS:-}"
SUGGEST_MENU="${SUGGEST_MENU:-false}"
STRICT_LLM="${STRICT_LLM:-false}"
PULL_IMAGE="${PULL_IMAGE:-true}"
IMAGE_UID="${IMAGE_UID:-10001}"
IMAGE_GID="${IMAGE_GID:-999}"
ENV_FILE="${ENV_FILE:-$APP_DIR/food_ai.env}"

rotate_log

command -v docker >/dev/null 2>&1 || die "docker не найден в PATH (systemd/cron имеет урезанное окружение)"

# ---------- 2. Каталог отчётов и права ----------
# Образ работает от appuser (UID 10001 / GID 999). Каталог, созданный root'ом
# с правами 755, контейнеру НЕ доступен на запись — будет PermissionError.
mkdir -p "$REPORTS_DIR" || die "не удалось создать $REPORTS_DIR"
owner="$(stat -c '%u:%g' "$REPORTS_DIR" 2>/dev/null || echo '?')"
if [ "$owner" != "$IMAGE_UID:$IMAGE_GID" ]; then
    if [ "$(id -u)" -eq 0 ]; then
        log "Права $REPORTS_DIR: $owner -> $IMAGE_UID:$IMAGE_GID (иначе контейнер не сможет писать)"
        chown "$IMAGE_UID:$IMAGE_GID" "$REPORTS_DIR" || die "chown $REPORTS_DIR не удался"
    else
        log "ВНИМАНИЕ: $REPORTS_DIR принадлежит $owner, контейнер работает от $IMAGE_UID:$IMAGE_GID"
    fi
fi
chmod 0755 "$REPORTS_DIR" 2>/dev/null || true

# ---------- 3. Обновление образа ----------
if [ "$PULL_IMAGE" = "true" ]; then
    if docker pull "$IMAGE" >/dev/null 2>&1; then
        log "Образ обновлён: $IMAGE"
    else
        # pull не фатален: работаем на кэшированном образе, сбой виден в журнале.
        log "ВНИМАНИЕ: docker pull $IMAGE не удался, использую локальную копию"
    fi
fi
docker image inspect "$IMAGE" >/dev/null 2>&1 \
    || die "образ $IMAGE отсутствует локально (и pull не удался)"

# ---------- 4. Секреты ----------
docker_env=()
if [ -f "$ENV_FILE" ]; then
    perms="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo '?')"
    if [ "$perms" != "600" ] && [ "$perms" != "400" ]; then
        log "ВНИМАНИЕ: $ENV_FILE имеет права $perms, ожидаются 600"
        [ "$(id -u)" -eq 0 ] && chmod 600 "$ENV_FILE" 2>/dev/null
    fi
    if [ -s "$ENV_FILE" ]; then
        docker_env+=(--env-file "$ENV_FILE")
    fi
else
    log "ВНИМАНИЕ: нет $ENV_FILE — POS-источники и DeepSeek будут недоступны"
fi

# ---------- 5. Аргументы CLI ----------
cli_args=(--inventory "$INVENTORY" --profile "$PROFILE" --warning-days "$WARNING_DAYS"
          --out reports/expiry_report.md --html-out reports/expiry_report.html
          --json-out reports/expiry_report.json)
[ "$SUGGEST_MENU" = "true" ] && cli_args+=(--suggest-menu)
[ "$STRICT_LLM" = "true" ] && cli_args+=(--strict-llm)
# EXTRA_ARGS — строка из конфига; намеренно разбивается по пробелам (glob отключён).
if [ -n "$EXTRA_ARGS" ]; then
    set -f
    # shellcheck disable=SC2206
    extra=($EXTRA_ARGS)
    set +f
    cli_args+=("${extra[@]}")
fi

docker_args=(run --rm
    -v "$REPORTS_DIR:/app/reports"
    -e "TZ=$TIMEZONE")          # без TZ контейнер считает «сегодня» по UTC
[ -d "$DATA_DIR" ] && docker_args+=(-v "$DATA_DIR:/app/data:ro")
# Безопасное раскрытие для пустого массива (bash >= 4.4 справился бы и без этого).
docker_args+=(${docker_env[@]+"${docker_env[@]}"} "$IMAGE")

log "Запуск: $IMAGE (TZ=$TIMEZONE, inventory=$INVENTORY, warning-days=$WARNING_DAYS)"
output="$(docker "${docker_args[@]}" "${cli_args[@]}" 2>&1)"
status=$?
[ -n "$output" ] && log "$output"

if [ "$status" -ne 0 ]; then
    log "Контейнер завершился с кодом $status — отчёт НЕ обновлён"
    exit "$status"
fi

# ---------- 6. Проверка результата ----------
missing=""
for f in expiry_report.md expiry_report.html; do
    [ -s "$REPORTS_DIR/$f" ] || missing="$missing $f"
done
[ -z "$missing" ] || die "в $REPORTS_DIR нет ожидаемых файлов:$missing"

log "Отчёт записан: $REPORTS_DIR/expiry_report.{md,html,json}"

# ---------- 7. Необязательная публикация (FTP/SFTP) ----------
# Нужна только если отчёт дополнительно выкладывается на обычный хостинг.
# Учётные данные — в publish.env (600), в репозиторий не попадают.
PUBLISH_ENV="${PUBLISH_ENV:-$APP_DIR/publish.env}"
if [ -f "$PUBLISH_ENV" ]; then
    # shellcheck source=/dev/null
    . "$PUBLISH_ENV"
    if [ "${PUBLISH_ENABLED:-false}" = "true" ]; then
        : "${PUBLISH_URL:?в $PUBLISH_ENV нет PUBLISH_URL}"
        ok=1
        for f in expiry_report.html expiry_report.md expiry_report.json; do
            if curl --fail --silent --show-error --upload-file "$REPORTS_DIR/$f" \
                    "$PUBLISH_URL/$f" >>"$LOG_FILE" 2>&1; then
                log "Опубликовано: $PUBLISH_URL/$f"
            else
                log "ВНИМАНИЕ: не удалось опубликовать $f"
                ok=0
            fi
        done
        [ "$ok" -eq 1 ] || log "Публикация выполнена не полностью"
    fi
fi

exit 0
