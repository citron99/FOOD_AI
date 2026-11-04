"""Проверка публикации статических файлов по HTTP с автоповтором.

Workflow публикует отчёт по FTP и затем сверяет каждый файл с локальной
сборкой. Хостинг периодически отвечает 503 на время обслуживания, поэтому
пятисотки и сетевые ошибки считаются временными и проверяются повторно.
Несовпадение sha256 и 4xx повтором не лечатся: это ошибка публикации.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path

DEFAULT_ATTEMPTS = 12
DEFAULT_DELAY = 300.0
FETCH_TIMEOUT = 30.0
TRANSIENT_STATUSES = frozenset({408, 429})


class PublishCheckError(RuntimeError):
    """Публикация не подтверждена; повтор не поможет."""

    @classmethod
    def bad_pair(cls, raw: str) -> PublishCheckError:
        return cls(f"Пара «{raw}» должна иметь вид remote:local")

    @classmethod
    def unreadable_local(cls, path: Path, error: object) -> PublishCheckError:
        return cls(f"Не удалось прочитать локальный файл {path}: {error}")

    @classmethod
    def unexpected_status(cls, url: str, status: int) -> PublishCheckError:
        return cls(f"{url} вернул HTTP {status}")

    @classmethod
    def mismatch(cls, pair: Pair, remote_sum: str, remote_size: int,
                 local_sum: str, local_size: int) -> PublishCheckError:
        return cls(
            f"{pair.remote} не совпадает с {pair.local}: "
            f"sha256 {remote_sum} ≠ {local_sum}, "
            f"размеры: удалённый {remote_size}, локальный {local_size}"
        )

    @classmethod
    def exhausted(cls, attempts: int, error: PublishCheckError) -> PublishCheckError:
        return cls(f"публикация не подтверждена (попыток: {attempts}): {error}")

    @classmethod
    def bad_attempts(cls) -> PublishCheckError:
        return cls("Число попыток должно быть не меньше 1")

    @classmethod
    def bad_delay(cls) -> PublishCheckError:
        return cls("Задержка между попытками не может быть отрицательной")


class TransientPublishError(PublishCheckError):
    """Временная ошибка: хостинг недоступен, имеет смысл повторить."""

    @classmethod
    def transient_status(cls, url: str, status: int) -> TransientPublishError:
        return cls(f"{url} вернул HTTP {status}")

    @classmethod
    def network(cls, url: str, error: object) -> TransientPublishError:
        return cls(f"{url}: {error}")


@dataclass(frozen=True)
class Pair:
    remote: str
    local: Path


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = DEFAULT_ATTEMPTS
    delay: float = DEFAULT_DELAY

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise PublishCheckError.bad_attempts()
        if self.delay < 0:
            raise PublishCheckError.bad_delay()


Fetch = Callable[[str], tuple[int, bytes]]
Sleep = Callable[[float], None]


def parse_pair(raw: str) -> Pair:
    remote, separator, local = raw.partition(":")
    if not separator or not remote or not local:
        raise PublishCheckError.bad_pair(raw)
    return Pair(remote=remote, local=Path(local))


def http_fetch(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    try:
        response = urllib.request.urlopen(request, timeout=FETCH_TIMEOUT)
    except urllib.error.HTTPError as error:
        return error.code, b""
    with response:
        return response.status, response.read()


def _fetch(fetch: Fetch, url: str) -> tuple[int, bytes]:
    try:
        result = fetch(url)
    except OSError as error:
        raise TransientPublishError.network(url, error) from error
    return result


def _read_local(pair: Pair) -> bytes:
    try:
        data = pair.local.read_bytes()
    except OSError as error:
        raise PublishCheckError.unreadable_local(pair.local, error) from error
    return data


def check_once(base: str, pairs: Sequence[Pair], fetch: Fetch, *, cache_buster: str) -> None:
    for pair in pairs:
        url = f"{base}/{pair.remote}?t={cache_buster}"
        status, body = _fetch(fetch, url)
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR or status in TRANSIENT_STATUSES:
            raise TransientPublishError.transient_status(url, status)
        if status != HTTPStatus.OK:
            raise PublishCheckError.unexpected_status(url, status)
        expected = _read_local(pair)
        remote_sum = hashlib.sha256(body).hexdigest()
        local_sum = hashlib.sha256(expected).hexdigest()
        if remote_sum != local_sum:
            raise PublishCheckError.mismatch(
                pair, remote_sum, len(body), local_sum, len(expected),
            )
        print(f"OK {base}/{pair.remote} HTTP 200, sha256 совпадает")


def _attempt(
    base: str, pairs: Sequence[Pair], fetch: Fetch, cache_buster: str,
) -> TransientPublishError | None:
    try:
        check_once(base, pairs, fetch, cache_buster=cache_buster)
    except TransientPublishError as error:
        return error
    return None


def check_publication(
    base: str,
    pairs: Sequence[Pair],
    *,
    fetch: Fetch = http_fetch,
    sleep: Sleep = time.sleep,
    policy: RetryPolicy | None = None,
) -> None:
    retry = policy if policy is not None else RetryPolicy()
    for attempt in range(1, retry.attempts + 1):
        error = _attempt(base, pairs, fetch, str(int(time.time())))
        if error is None:
            return
        if attempt == retry.attempts:
            raise PublishCheckError.exhausted(retry.attempts, error)
        print(
            f"Попытка {attempt}/{retry.attempts}: {error}; "
            f"повтор через {retry.delay:g} с",
            file=sys.stderr,
        )
        sleep(retry.delay)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Проверить опубликованные файлы по HTTP и сверить sha256",
    )
    parser.add_argument("--base", required=True, help="базовый URL публикации")
    parser.add_argument(
        "--pair", action="append", required=True, metavar="REMOTE:LOCAL",
        help="путь на сайте и локальный файл; можно указывать несколько раз",
    )
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS,
                        help="всего попыток при временных ошибках")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help="пауза между попытками, секунд")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    fetch: Fetch = http_fetch,
    sleep: Sleep = time.sleep,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        pairs = [parse_pair(raw) for raw in args.pair]
        policy = RetryPolicy(attempts=args.attempts, delay=args.delay)
        check_publication(
            args.base.rstrip("/"), pairs, fetch=fetch, sleep=sleep, policy=policy,
        )
    except PublishCheckError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
