import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from agent.adapters.rkeeper import (
    ImportResult,
    RkeeperConfig,
    RkeeperError,
    RkeeperResponseError,
    fetch_product_directory,
    import_products,
    map_rk_item,
)

CONFIG = RkeeperConfig(
    base_url="http://rk7.local:8080",
    station="RK7",
    username="sk_agent",
    password="secret",
)

_SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<RK7QueryResult Status="Ok">
  <Products>
    <Item ID="1" Code="101" Name="Молоко 3.2%" UnitName="л"/>
    <Item ID="2" Code="102" Name="Куриное филе" UnitName="кг"/>
  </Products>
</RK7QueryResult>""".encode("utf-8")


def fake_transport(payload: bytes):
    def transport(url, body, headers, timeout):
        assert url == "http://rk7.local:8080/rk7api/v1"
        assert headers["Authorization"].startswith("Basic ")
        return payload
    return transport


class RkeeperAdapterTests(unittest.TestCase):
    def test_fetch_parses_directory_items(self):
        items = fetch_product_directory(CONFIG, fake_transport(_SAMPLE_XML))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["Name"], "Молоко 3.2%")

    def test_map_item_to_product_is_safe_by_default(self):
        product = map_rk_item({"Name": "Молоко 3.2%", "UnitName": "л"}, CONFIG)
        self.assertEqual(product.name, "Молоко 3.2%")
        self.assertEqual(product.unit, "л")
        # Без выдуманных данных: нет остатка и срока годности.
        self.assertEqual(product.quantity, 0.0)
        self.assertIsNone(product.expires_at)
        self.assertEqual(product.status, "active")

    def test_item_without_name_is_rejected(self):
        with self.assertRaisesRegex(RkeeperResponseError, "без наименования"):
            map_rk_item({"Code": "101"}, CONFIG)

    def test_query_error_status_raises(self):
        payload = '<RK7QueryResult Status="QueryError" ErrorText="Нет доступа"/>'.encode("utf-8")
        with self.assertRaisesRegex(RkeeperResponseError, "Нет доступа"):
            fetch_product_directory(CONFIG, fake_transport(payload))

    def test_invalid_xml_raises(self):
        with self.assertRaisesRegex(RkeeperResponseError, "XML"):
            fetch_product_directory(CONFIG, fake_transport(b"<broken"))

    def test_empty_directory_raises(self):
        payload = b'<RK7QueryResult Status="Ok"><Products/></RK7QueryResult>'
        with self.assertRaisesRegex(RkeeperResponseError, "пуст"):
            fetch_product_directory(CONFIG, fake_transport(payload))

    def test_import_counts_skipped_items(self):
        payload = _SAMPLE_XML.replace(
            '<Item ID="2" Code="102" Name="Куриное филе" UnitName="кг"/>'.encode("utf-8"),
            '<Item ID="2" Code="102" UnitName="кг"/>'.encode("utf-8"),
        )
        result = import_products(CONFIG, fake_transport(payload))
        self.assertIsInstance(result, ImportResult)
        self.assertEqual(result.total, 2)
        self.assertEqual(result.imported, 1)
        self.assertEqual(result.skipped, 1)


if __name__ == "__main__":
    unittest.main()
