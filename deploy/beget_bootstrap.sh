#!/usr/bin/env bash
# Сборка Python 3.11 в ~/.local на виртуальном хостинге Beget.
#
# Зачем: проект требует Python >= 3.11 (pyproject.toml), а на shared-хостинге
# Beget из коробки доступны только /usr/bin/python2.7, python3.6 и python3.7
# (https://beget.com/ru/kb/how-to/web-apps/python). На python3.7 агент даже не
# импортируется: agent/adapters/base.py:32 вычисляет PEP 604-алиас
# `Transport = Callable[[str, bytes | None, ...], bytes]` → TypeError.
#
# Порядок запуска (ВАЖНО — сборка выполняется внутри Docker-контейнера аккаунта):
#   ssh <логин>@<логин>.beget.tech      # вход на сервер хостинга
#   ssh localhost -p222                 # вход в контейнер аккаунта
#   bash deploy/beget_bootstrap.sh --project ~/FOOD_AI
#
# Скрипт повторяет документированную Beget процедуру «Локальная установка Python»:
# сначала openssl 1.1.1 в ~/.local (без него Python >= 3.10 не соберётся с ssl),
# затем сам Python. Ничего за пределами $HOME не меняется, root не нужен.
#
# Флаги:
#   --project DIR   после сборки прогнать тесты проекта и пробный отчёт из DIR
#   --force         собирать, даже если подходящий python3 уже найден
#   --optimizations добавить --enable-optimizations (PGO): сборка в разы дольше,
#                   для ежедневного отчёта ускорение не нужно
#   --dry-run       только печатать команды, ничего не выполнять
#
# Переменные окружения: PY_VERSION (3.11.13), OPENSSL_VERSION (1.1.1w),
# OPENSSL_URL, PYTHON_URL, JOBS, PREFIX ($HOME/.local).
set -euo pipefail

PY_VERSION="${PY_VERSION:-3.11.13}"
OPENSSL_VERSION="${OPENSSL_VERSION:-1.1.1w}"
# Проверено 2026-09-29: оба URL отдают 200. openssl.org/source/openssl-1.1.1w.tar.gz
# тоже работает (301 на этот же релиз GitHub), а .../openssl-1.1.1l.tar.gz — версия
# из документации Beget.
OPENSSL_URL="${OPENSSL_URL:-https://github.com/openssl/openssl/releases/download/OpenSSL_1_1_1${OPENSSL_VERSION#1.1.1}/openssl-${OPENSSL_VERSION}.tar.gz}"
PYTHON_URL="${PYTHON_URL:-https://www.python.org/ftp/python/${PY_VERSION}/Python-${PY_VERSION}.tgz}"
JOBS="${JOBS:-$(( $(nproc 2>/dev/null || echo 4) / 4 ))}"
[ "${JOBS}" -ge 1 ] || JOBS=1
PREFIX="${PREFIX:-$HOME/.local}"
OPTIMIZATIONS=0
DRY_RUN=0
FORCE=0
PROJECT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --project)      PROJECT="${2:?нужен путь к каталогу проекта}"; shift 2 ;;
        --force)        FORCE=1; shift ;;
        --optimizations) OPTIMIZATIONS=1; shift ;;
        --dry-run)      DRY_RUN=1; shift ;;
        -h|--help)      sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "Неизвестный аргумент: $1" >&2; exit 2 ;;
    esac
done

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "+ $*"
    else
        "$@"
    fi
}

have_modern_python() {
    # Ищем интерпретатор >= 3.11: сначала собранный локально, затем системный.
    for candidate in "$PREFIX/bin/python3.11" "$PREFIX/bin/python3" \
                     python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
                FOUND_PY="$(command -v "$candidate")"
                return 0
            fi
        fi
    done
    return 1
}

echo "=== Beget bootstrap: Python ${PY_VERSION} в ${PREFIX} ==="
echo "Хост: $(hostname 2>/dev/null || echo '?'); пользователь: $(id -un); потоков сборки: ${JOBS}"
if [ ! -d "$HOME/.beget" ] && [ "$DRY_RUN" -eq 0 ]; then
    echo "ВНИМАНИЕ: каталог ~/.beget не найден — вероятно, вы НЕ внутри Docker-контейнера"
    echo "аккаунта. Сначала выполните: ssh <логин>@<логин>.beget.tech, затем ssh localhost -p222."
fi

if have_modern_python && [ "$FORCE" -eq 0 ]; then
    echo "Подходящий интерпретатор уже есть: $FOUND_PY ($("$FOUND_PY" -V 2>&1))"
    echo "Сборка не требуется. Повторить принудительно: --force"
else
    echo "Подходящего Python (>= 3.11) не найдено — собираем из исходников."
    if [ "$DRY_RUN" -eq 0 ]; then
        for tool in wget tar make gcc; do
            command -v "$tool" >/dev/null 2>&1 || { echo "Нет утилиты $tool — сборка невозможна." >&2; exit 3; }
        done
    fi

    TMPDIR_BUILD="$HOME/.beget/tmp"
    run mkdir -p "$TMPDIR_BUILD"

    # --- 1. OpenSSL 1.1.1 (нужен для Python >= 3.10: модуль ssl и HTTPS) ---
    if [ ! -x "$PREFIX/bin/openssl" ]; then
        run bash -c "cd '$TMPDIR_BUILD' && wget -q --show-progress -O openssl.tar.gz '$OPENSSL_URL'"
        run bash -c "cd '$TMPDIR_BUILD' && tar -xzf openssl.tar.gz"
        run bash -c "cd '$TMPDIR_BUILD/openssl-${OPENSSL_VERSION}' && ./config \
            --prefix='$PREFIX' --openssldir='$PREFIX/ssl' \
            '-Wl,--enable-new-dtags,-rpath,\$(LIBRPATH)'"
        run bash -c "cd '$TMPDIR_BUILD/openssl-${OPENSSL_VERSION}' && make -j${JOBS} && make install"
        run bash -c "'$PREFIX/bin/openssl' version"
    else
        echo "OpenSSL уже собран: $("$PREFIX/bin/openssl" version 2>&1)"
    fi

    # --- 2. Python ---
    run bash -c "cd '$TMPDIR_BUILD' && wget -q --show-progress -O python.tar.gz '$PYTHON_URL'"
    run bash -c "cd '$TMPDIR_BUILD' && tar -xzf python.tar.gz"
    configure_flags="--prefix=$PREFIX --with-openssl=$PREFIX --with-openssl-rpath=auto --enable-loadable-sqlite-extensions"
    [ "$OPTIMIZATIONS" -eq 1 ] && configure_flags="$configure_flags --enable-optimizations"
    run bash -c "cd '$TMPDIR_BUILD/Python-${PY_VERSION}' && ./configure $configure_flags \
        LDFLAGS='-Wl,-rpath /usr/local/lib'"
    run bash -c "cd '$TMPDIR_BUILD/Python-${PY_VERSION}' && make -j${JOBS} && make install"
fi

# --- 3. Проверка результата ---
PY="$PREFIX/bin/python3.11"
if [ "$DRY_RUN" -eq 1 ]; then
    echo "+ $PY -V && $PY -c 'import ssl, zlib, hashlib; print(ssl.OPENSSL_VERSION)'"
    exit 0
fi
[ -x "$PY" ] || { echo "Сборка завершилась, но $PY не появился." >&2; exit 4; }
echo "=== Проверка ==="
"$PY" -V
# ssl обязателен: без него не работают HTTPS-запросы к POS-облакам и DeepSeek.
"$PY" - <<'PYCHECK'
import ssl, zlib, hashlib
print("ssl:", ssl.OPENSSL_VERSION)
print("zlib:", zlib.ZLIB_VERSION)
print("hashlib ok:", bool(hashlib.sha256))
PYCHECK

if [ -n "$PROJECT" ]; then
    echo "=== Проект: $PROJECT ==="
    [ -d "$PROJECT" ] || { echo "Каталог проекта не найден: $PROJECT" >&2; exit 5; }
    run bash -c "cd '$PROJECT' && '$PY' -m unittest discover -s tests"
    run bash -c "cd '$PROJECT' && mkdir -p reports && '$PY' -m agent.cli \
        --inventory data/inventory.json --warning-days 3 \
        --out reports/expiry_report.md --html-out reports/expiry_report.html"
    echo "Готово. Отчёт: $PROJECT/reports/expiry_report.html"
    echo "Путь к интерпретатору для заданий cron: $PY"
fi
