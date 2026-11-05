"""Автоповтор проверки публикации: 5xx/сеть и устаревшая версия файла повторяются, 4xx — нет."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError

from agent.publish_check import (
    Pair,
    PublishCheckError,
    RetryPolicy,
    check_publication,
    main,
    parse_pair,
)

CONTENT = "отчёт".encode()


class FakeFetch:
    def __init__(self, responses: list[tuple[int, bytes] | Exception]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def __call__(self, url: str) -> tuple[int, bytes]:
        self.urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def no_sleep(_: float) -> None:
    return None


class PublishCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.local = Path(self._directory.name) / "index.html"
        self.local.write_bytes(CONTENT)
        self.pair = Pair(remote="index.html", local=self.local)

    def check(
        self,
        fetch: FakeFetch,
        *,
        attempts: int = 1,
        delay: float = 0.0,
        mismatch_attempts: int = 1,
        mismatch_delay: float = 0.0,
    ) -> list[float]:
        sleeps: list[float] = []
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            check_publication(
                "http://example.test", [self.pair], fetch=fetch, sleep=sleeps.append,
                policy=RetryPolicy(
                    attempts=attempts,
                    delay=delay,
                    mismatch_attempts=mismatch_attempts,
                    mismatch_delay=mismatch_delay,
                ),
            )
        return sleeps

    def test_success_on_first_attempt_does_not_sleep(self) -> None:
        fetch = FakeFetch([(200, CONTENT)])
        self.assertEqual(self.check(fetch, attempts=3), [])

    def test_5xx_is_retried_until_success(self) -> None:
        fetch = FakeFetch([(503, b""), (502, b""), (200, CONTENT)])
        sleeps = self.check(fetch, attempts=3, delay=7.0)
        self.assertEqual(sleeps, [7.0, 7.0])
        self.assertEqual(len(fetch.urls), 3)

    def test_throttling_statuses_are_retried(self) -> None:
        for status in (408, 429):
            with self.subTest(status=status):
                fetch = FakeFetch([(status, b""), (200, CONTENT)])
                self.assertEqual(self.check(fetch, attempts=2), [0.0])

    def test_network_error_is_retried(self) -> None:
        fetch = FakeFetch([URLError("connection reset"), (200, CONTENT)])
        self.assertEqual(self.check(fetch, attempts=2), [0.0])

    def test_4xx_is_not_retried(self) -> None:
        fetch = FakeFetch([(404, b"")])
        with (redirect_stdout(io.StringIO()),
              self.assertRaisesRegex(PublishCheckError, "HTTP 404")):
            check_publication(
                "http://example.test", [self.pair], fetch=fetch, sleep=no_sleep,
                policy=RetryPolicy(attempts=3, delay=0),
            )
        self.assertEqual(len(fetch.urls), 1)

    def test_stale_content_is_retried_until_fresh(self) -> None:
        fetch = FakeFetch([(200, b"old report"), (200, CONTENT)])
        sleeps = self.check(fetch, mismatch_attempts=2, mismatch_delay=60.0)
        self.assertEqual(sleeps, [60.0])
        self.assertEqual(len(fetch.urls), 2)

    def test_stale_content_exhausts_its_own_budget(self) -> None:
        fetch = FakeFetch([(200, b"old report")] * 3)
        with (redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
              self.assertRaisesRegex(PublishCheckError, r"попыток: 3.*не совпадает")):
            check_publication(
                "http://example.test", [self.pair], fetch=fetch, sleep=no_sleep,
                policy=RetryPolicy(attempts=5, delay=0, mismatch_attempts=3, mismatch_delay=0),
            )
        self.assertEqual(len(fetch.urls), 3)

    def test_transient_and_stale_budgets_are_independent(self) -> None:
        fetch = FakeFetch([(503, b""), (200, b"old report"), (502, b""), (200, CONTENT)])
        sleeps = self.check(
            fetch, attempts=3, delay=5.0, mismatch_attempts=3, mismatch_delay=60.0,
        )
        self.assertEqual(sleeps, [5.0, 60.0, 5.0])
        self.assertEqual(len(fetch.urls), 4)

    def test_missing_local_file_is_permanent(self) -> None:
        missing = Pair(remote="index.html", local=self.local.with_name("gone.html"))
        fetch = FakeFetch([(200, CONTENT)])
        with (redirect_stdout(io.StringIO()),
              self.assertRaisesRegex(PublishCheckError, "Не удалось прочитать")):
            check_publication(
                "http://example.test", [missing], fetch=fetch, sleep=no_sleep,
                policy=RetryPolicy(attempts=3, delay=0),
            )
        self.assertEqual(len(fetch.urls), 1)

    def test_exhausted_attempts_report_count_and_status(self) -> None:
        fetch = FakeFetch([(503, b"")] * 3)
        sleeps: list[float] = []
        with (redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
              self.assertRaisesRegex(PublishCheckError, r"попыток: 3.*HTTP 503")):
            check_publication(
                "http://example.test", [self.pair], fetch=fetch, sleep=sleeps.append,
                policy=RetryPolicy(attempts=3, delay=0),
            )
        self.assertEqual(sleeps, [0.0, 0.0])
        self.assertEqual(len(fetch.urls), 3)

    def test_retry_policy_validates_values(self) -> None:
        with self.assertRaisesRegex(PublishCheckError, "не меньше 1"):
            RetryPolicy(attempts=0)
        with self.assertRaisesRegex(PublishCheckError, "не меньше 1"):
            RetryPolicy(mismatch_attempts=0)
        with self.assertRaisesRegex(PublishCheckError, "отрицательной"):
            RetryPolicy(delay=-1)
        with self.assertRaisesRegex(PublishCheckError, "отрицательной"):
            RetryPolicy(mismatch_delay=-1)

    def test_pair_requires_remote_and_local(self) -> None:
        for raw in ("index.html", ":local.html", "remote:"):
            with self.subTest(raw=raw), self.assertRaisesRegex(PublishCheckError, "remote:local"):
                parse_pair(raw)

    def test_main_reports_success(self) -> None:
        fetch = FakeFetch([(200, CONTENT)])
        with redirect_stdout(io.StringIO()) as stdout:
            code = main(
                ["--base", "http://example.test/", "--pair", f"index.html:{self.local}",
                 "--attempts", "1", "--delay", "0"],
                fetch=fetch, sleep=no_sleep,
            )
        self.assertEqual(code, 0)
        self.assertIn("sha256 совпадает", stdout.getvalue())
        self.assertTrue(fetch.urls[0].startswith("http://example.test/index.html?t="))

    def test_main_reports_failure_with_annotation(self) -> None:
        fetch = FakeFetch([(503, b"")])
        with redirect_stderr(io.StringIO()) as stderr:
            code = main(
                ["--base", "http://example.test", "--pair", f"index.html:{self.local}",
                 "--attempts", "1", "--delay", "0"],
                fetch=fetch, sleep=no_sleep,
            )
        self.assertEqual(code, 1)
        self.assertIn("::error::", stderr.getvalue())
        self.assertIn("503", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
