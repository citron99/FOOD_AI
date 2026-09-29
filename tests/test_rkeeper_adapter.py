import unittest

from agent.adapters.base import AdapterAuthError, AdapterError, AdapterResponseError
from agent.adapters.rkeeper import (
    ImportResult,
    RkeeperAuthError,
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
</RK7QueryResult>""".encode()


def fake_transport(payload: bytes):
    def transport(url, body, headers, timeout):
        assert url == "http://rk7.local:8080/rk7api/v1"
        assert headers["Authorization"].startswith("Basic ")
        return payload
    return transport


class RkeeperAdapterTests(unittest.TestCase):
    def test_errors_inherit_from_adapter_base(self):
        # Волна 5 (п.12): иерархия ошибок R:keeper — часть общей иерархии
        # адаптеров (ловится через AdapterError), имена сохранены.
        self.assertTrue(issubclass(RkeeperError, AdapterError))
        self.assertTrue(issubclass(RkeeperAuthError, AdapterAuthError))
        self.assertTrue(issubclass(RkeeperResponseError, AdapterResponseError))

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
        payload = '<RK7QueryResult Status="QueryError" ErrorText="Нет доступа"/>'.encode()
        with self.assertRaisesRegex(RkeeperResponseError, "Нет доступа"):
            fetch_product_directory(CONFIG, fake_transport(payload))

    def test_invalid_xml_raises(self):
        with self.assertRaisesRegex(RkeeperResponseError, "XML"):
            fetch_product_directory(CONFIG, fake_transport(b"<broken"))

    def test_doctype_with_internal_entities_is_rejected(self):
        # П.17 ревью: ET.fromstring раскрывает внутренние сущности (~1260-кратная
        # амплификация на этом пейлоаде — «billion laughs»). Ответ с DOCTYPE
        # должен быть отклонён до парсинга.
        payload = """<?xml version="1.0"?>
        <!DOCTYPE RK7QueryResult [
          <!ENTITY lol "lollollollollollollollollollol">
          <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
          <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
          <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
          <!ENTITY lolz "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
        ]>
        <RK7QueryResult Status="Ok">
          <Item ID="1" Code="101" Name="&lolz;" UnitName="шт"/>
        </RK7QueryResult>""".encode()
        with self.assertRaisesRegex(RkeeperResponseError, "DOCTYPE"):
            fetch_product_directory(CONFIG, fake_transport(payload))

    def test_doctype_with_external_entity_is_rejected(self):
        # XXE: внешние сущности ElementTree и так не подгружает, но ответ
        # должен отклоняться тем же понятным сообщением, без попытки чтения.
        payload = (
            '<!DOCTYPE RK7QueryResult [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            '<RK7QueryResult Status="Ok">'
            '<Item ID="1" Code="101" Name="&xxe;" UnitName="шт"/>'
            "</RK7QueryResult>"
        ).encode()
        with self.assertRaisesRegex(RkeeperResponseError, "DOCTYPE"):
            fetch_product_directory(CONFIG, fake_transport(payload))

    def test_lowercase_doctype_is_rejected(self):
        payload = (
            '<!doctype RK7QueryResult>'
            '<RK7QueryResult Status="Ok">'
            '<Item ID="1" Code="101" Name="Молоко" UnitName="л"/>'
            "</RK7QueryResult>"
        ).encode()
        with self.assertRaisesRegex(RkeeperResponseError, "DOCTYPE"):
            fetch_product_directory(CONFIG, fake_transport(payload))

    def test_oversize_response_is_rejected(self):
        config = RkeeperConfig(
            base_url="http://rk7.local:8080", station="RK7",
            username="u", password="p", max_response_bytes=64,
        )
        with self.assertRaisesRegex(RkeeperResponseError, "слишком велик"):
            fetch_product_directory(config, fake_transport(_SAMPLE_XML))

    def test_empty_directory_raises(self):
        payload = b'<RK7QueryResult Status="Ok"><Products/></RK7QueryResult>'
        with self.assertRaisesRegex(RkeeperResponseError, "пуст"):
            fetch_product_directory(CONFIG, fake_transport(payload))

    def test_import_counts_skipped_items(self):
        payload = _SAMPLE_XML.replace(
            '<Item ID="2" Code="102" Name="Куриное филе" UnitName="кг"/>'.encode(),
            '<Item ID="2" Code="102" UnitName="кг"/>'.encode(),
        )
        result = import_products(CONFIG, fake_transport(payload))
        self.assertIsInstance(result, ImportResult)
        self.assertEqual(result.total, 2)
        self.assertEqual(result.imported, 1)
        self.assertEqual(result.skipped, 1)


if __name__ == "__main__":
    unittest.main()
