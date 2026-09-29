import base64
import json
import os
import unittest

from agent.adapters import frontpad, iiko, quickresto, registry, saby, yuma
from agent.adapters.base import (
    AdapterAuthError,
    AdapterError,
    AdapterResponseError,
    RestConfig,
    basic_authorization,
)


def fake_json(payload):
    raw = json.dumps(payload).encode("utf-8")
    return lambda url, body, headers, timeout: raw


def spy_transport(captured):
    def transport(url, body, headers, timeout):
        captured["url"], captured["body"], captured["headers"] = url, body, headers
        return b"[]"
    return transport


IIKO_CONFIG = RestConfig(
    base_url="https://api-ru.iiko.services",
    endpoint="/api/1/nomenclature",
    token="test-token",
)

IIKO_SAMPLE = {
    "groups": [{"id": "g1", "name": "Молочные"}],
    "products": [
        {"id": "p1", "name": "Молоко 3.2%", "mainUnit": "л", "type": "Good"},
        {"id": "p2", "name": "Торт Наполеон", "mainUnit": {"name": "кг"}, "type": "Dish"},
        {"id": "p3", "mainUnit": "шт.", "type": "Good"},
    ],
    "success": True,
}

QR_CONFIG = RestConfig(
    base_url="https://demo.quickresto.ru",
    endpoint="/api/products",
    username="u",
    password="p",
    auth_kind="basic",
)

QR_SAMPLE = [
    {"name": "Кофе зерно", "unit": "кг"},
    {"unit": "шт."},
]

FP_CONFIG = RestConfig(
    base_url="https://demo.frontpad.ru",
    endpoint="/api/",
    token="test-secret",
)

FP_SAMPLE = [
    {"name": "Чизкейк", "unit": "шт."},
    {"unit": "кг"},
]

SABY_CONFIG = RestConfig(
    base_url="https://saby.example",
    endpoint="/api/v1/products",
    token="test-token",
)

SABY_SAMPLE = [
    {"name": "Сыр Гауда", "unit": "кг"},
    {"unit": "шт."},
]

YUMA_CONFIG = RestConfig(
    base_url="https://yuma.example",
    endpoint="/api/v1/products",
    token="test-token",
)

YUMA_SAMPLE = [
    {"name": "Латте зерно", "unit": "кг"},
    {"unit": "шт."},
]


class IikoAdapterTests(unittest.TestCase):
    def test_import_maps_products_and_skips_nameless(self):
        outcome = iiko.import_products(IIKO_CONFIG, fake_json(IIKO_SAMPLE))
        self.assertEqual(outcome.total, 3)
        self.assertEqual(outcome.imported, 2)
        self.assertEqual(outcome.skipped, 1)
        self.assertEqual(outcome.products[0].name, "Молоко 3.2%")
        self.assertEqual(outcome.products[0].unit, "л")
        self.assertEqual(outcome.products[1].unit, "кг")
        # Безопасный импорт: нет остатков и сроков годности.
        for product in outcome.products:
            self.assertEqual(product.quantity, 0.0)
            self.assertIsNone(product.expires_at)

    def test_unexpected_structure_raises(self):
        with self.assertRaisesRegex(AdapterResponseError, "неожиданную структуру"):
            iiko.import_products(IIKO_CONFIG, fake_json({"error": "x"}))


class QuickRestoAdapterTests(unittest.TestCase):
    def test_import_maps_products_and_skips_nameless(self):
        outcome = quickresto.import_products(QR_CONFIG, fake_json(QR_SAMPLE))
        self.assertEqual(outcome.imported, 1)
        self.assertEqual(outcome.skipped, 1)
        self.assertEqual(outcome.products[0].name, "Кофе зерно")
        self.assertEqual(outcome.products[0].unit, "кг")

    def test_unexpected_structure_raises(self):
        with self.assertRaisesRegex(AdapterResponseError, "неожиданную структуру"):
            quickresto.import_products(QR_CONFIG, fake_json({"items": []}))

    def test_basic_auth_header_comes_from_config(self):
        # Волна 5 (п.14): Basic собирает RestConfig.auth_headers(), а не адаптер.
        captured = {}
        quickresto.import_products(QR_CONFIG, spy_transport(captured))
        self.assertEqual(
            captured["headers"].get("Authorization"),
            basic_authorization("u:p"),
        )


class FrontPadAdapterTests(unittest.TestCase):
    def test_import_maps_products_and_skips_nameless(self):
        outcome = frontpad.import_products(FP_CONFIG, fake_json(FP_SAMPLE))
        self.assertEqual(outcome.imported, 1)
        self.assertEqual(outcome.skipped, 1)
        self.assertEqual(outcome.products[0].name, "Чизкейк")
        self.assertEqual(outcome.products[0].unit, "шт.")

    def test_unexpected_structure_raises(self):
        with self.assertRaisesRegex(AdapterResponseError, "неожиданную структуру"):
            frontpad.import_products(FP_CONFIG, fake_json({"items": []}))

    def test_secret_sent_only_in_authorization_header(self):
        # Волна 5 (п.14): секрет ровно в одном канале — заголовке Bearer;
        # в теле формы он больше не дублируется.
        captured = {}
        frontpad.import_products(
            RestConfig(base_url="https://demo.frontpad.ru", endpoint="/api/",
                       token="SUPERSECRET123"),
            spy_transport(captured),
        )
        self.assertNotIn(b"secret=", captured["body"])
        self.assertIn(b"cmd=getProducts", captured["body"])
        self.assertEqual(captured["headers"].get("Authorization"),
                         "Bearer SUPERSECRET123")


class SabyYumaAdapterTests(unittest.TestCase):
    def test_saby_import_maps_products_and_skips_nameless(self):
        outcome = saby.import_products(SABY_CONFIG, fake_json(SABY_SAMPLE))
        self.assertEqual(outcome.imported, 1)
        self.assertEqual(outcome.skipped, 1)
        self.assertEqual(outcome.products[0].name, "Сыр Гауда")
        self.assertEqual(outcome.products[0].location, "Saby Presto (справочник)")

    def test_yuma_import_maps_products_and_skips_nameless(self):
        outcome = yuma.import_products(YUMA_CONFIG, fake_json(YUMA_SAMPLE))
        self.assertEqual(outcome.imported, 1)
        self.assertEqual(outcome.skipped, 1)
        self.assertEqual(outcome.products[0].name, "Латте зерно")
        self.assertEqual(outcome.products[0].location, "YUMA (справочник)")

    def test_unexpected_structure_raises(self):
        with self.assertRaisesRegex(AdapterResponseError, "неожиданную структуру"):
            saby.import_products(SABY_CONFIG, fake_json({"items": []}))
        with self.assertRaisesRegex(AdapterResponseError, "неожиданную структуру"):
            yuma.import_products(YUMA_CONFIG, fake_json({"items": []}))

    def test_mapping_uses_passed_config_not_environ(self):
        # Волна 5 (п.15): маппинг зависит от переданного конфига, а не читает
        # os.environ на каждую позицию.
        os.environ["SABY_NAME_FIELD"] = "bogus_field"
        os.environ["YUMA_NAME_FIELD"] = "bogus_field"
        try:
            cfg = RestConfig(base_url="https://saby.example", endpoint="/e",
                             token="t", name_field="title", unit_field="u")
            outcome = saby.import_products(cfg, fake_json([{"title": "Тофу", "u": "кг"}]))
            self.assertEqual(outcome.imported, 1)
            self.assertEqual(outcome.products[0].name, "Тофу")
            self.assertEqual(outcome.products[0].unit, "кг")
            outcome = yuma.import_products(cfg, fake_json([{"title": "Сейтан", "u": "кг"}]))
            self.assertEqual(outcome.products[0].name, "Сейтан")
        finally:
            os.environ.pop("SABY_NAME_FIELD", None)
            os.environ.pop("YUMA_NAME_FIELD", None)


class RestConfigAuthTests(unittest.TestCase):
    def test_bearer_is_default(self):
        cfg = RestConfig(base_url="https://x", endpoint="/", token="tok")
        self.assertEqual(cfg.auth_headers(), {"Authorization": "Bearer tok"})

    def test_basic_header_built_by_config(self):
        cfg = RestConfig(base_url="https://x", endpoint="/", auth_kind="basic",
                         username="u", password="p")
        expected = "Basic " + base64.b64encode(b"u:p").decode("ascii")
        self.assertEqual(cfg.auth_headers(), {"Authorization": expected})

    def test_none_sends_no_secret(self):
        cfg = RestConfig(base_url="https://x", endpoint="/", auth_kind="none", token="tok")
        self.assertEqual(cfg.auth_headers(), {})


class RegistryTests(unittest.TestCase):
    def test_lists_supported_sources(self):
        sources = registry.available_sources()
        for expected in ("rkeeper", "iiko", "quickresto", "frontpad", "saby", "yuma"):
            self.assertIn(expected, sources)

    def test_unknown_source_raises_with_list(self):
        with self.assertRaisesRegex(AdapterError, "Доступные"):
            registry.load_source("unknown")

    def test_load_source_reports_missing_env_as_auth_error(self):
        # Волна 5 (п.16): отсутствие переменных окружения — ошибка
        # конфигурации доступа (AdapterAuthError), а не ответа системы.
        env_vars = (
            "IIKO_API_TOKEN", "IIKO_BASE_URL",
            "QR_BASE_URL", "QR_USER", "QR_PASSWORD",
            "FRONTPAD_BASE_URL", "FRONTPAD_SECRET",
            "SABY_BASE_URL", "SABY_API_TOKEN",
            "YUMA_BASE_URL", "YUMA_API_TOKEN",
        )
        saved = {var: os.environ.pop(var, None) for var in env_vars}
        try:
            checks = (
                ("iiko", "IIKO_API_TOKEN"),
                ("quickresto", "QR_USER"),
                ("frontpad", "FRONTPAD_SECRET"),
                ("saby", "SABY_BASE_URL"),
                ("yuma", "YUMA_API_TOKEN"),
            )
            for source, env_var in checks:
                with self.subTest(source=source), self.assertRaisesRegex(AdapterAuthError, env_var):
                    registry.load_source(source)
        finally:
            for var, value in saved.items():
                if value is not None:
                    os.environ[var] = value


if __name__ == "__main__":
    unittest.main()
