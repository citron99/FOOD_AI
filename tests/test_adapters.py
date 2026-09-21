import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from agent.adapters.base import AdapterError, AdapterResponseError, RestConfig
from agent.adapters import iiko, quickresto, registry


def fake_json(payload):
    raw = json.dumps(payload).encode("utf-8")
    return lambda url, body, headers, timeout: raw


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
)

QR_SAMPLE = [
    {"name": "Кофе зерно", "unit": "кг"},
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


class RegistryTests(unittest.TestCase):
    def test_lists_supported_sources(self):
        sources = registry.available_sources()
        for expected in ("rkeeper", "iiko", "quickresto", "frontpad", "saby", "yuma"):
            self.assertIn(expected, sources)

    def test_unknown_source_raises_with_list(self):
        with self.assertRaisesRegex(AdapterError, "Доступные"):
            registry.load_source("unknown")

    def test_load_source_reports_missing_env(self):
        for var in ("IIKO_API_TOKEN",):
            os.environ.pop(var, None)
        with self.assertRaisesRegex(AdapterResponseError, "IIKO_API_TOKEN"):
            registry.load_source("iiko")


if __name__ == "__main__":
    unittest.main()
