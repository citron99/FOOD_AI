import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from agent.adapters.base import AdapterError, AdapterResponseError
from agent.adapters import deepseek
from agent.cli import Product


def fake_response(payload):
    raw = json.dumps(payload).encode("utf-8")
    return lambda url, body, headers, timeout: raw


class RecordingTransport:
    """Транспорт, запоминающий запрос: нужен для проверки содержимого промпта."""

    def __init__(self, payload):
        self.payload = payload
        self.url = None
        self.request = None

    def __call__(self, url, body, headers, timeout):
        self.url = url
        self.request = json.loads(body.decode("utf-8"))
        return json.dumps(self.payload).encode("utf-8")

    @property
    def prompt(self) -> str:
        return self.request["messages"][0]["content"]


CHAT_OK = {
    "choices": [{"message": {"role": "assistant", "content": "1. Омлет с молоком"}}],
    "usage": {"total_tokens": 10},
}


class DeepSeekAdapterTests(unittest.TestCase):
    def test_chat_returns_content(self):
        reply = deepseek.chat(
            [{"role": "user", "content": "hi"}],
            api_key="test-key",
            transport=fake_response(CHAT_OK),
        )
        self.assertEqual(reply, "1. Омлет с молоком")

    def test_chat_requires_api_key(self):
        import os
        os.environ.pop("DEEPSEEK_API_KEY", None)
        with self.assertRaisesRegex(AdapterError, "DEEPSEEK_API_KEY"):
            deepseek.chat([{"role": "user", "content": "hi"}])

    def test_chat_without_choices_raises(self):
        with self.assertRaisesRegex(AdapterResponseError, "choices"):
            deepseek.chat(
                [{"role": "user", "content": "hi"}],
                api_key="k", transport=fake_response({"object": "chat.completion"}),
            )

    def test_chat_with_empty_content_raises(self):
        payload = {"choices": [{"message": {"role": "assistant", "content": ""}}]}
        with self.assertRaisesRegex(AdapterResponseError, "пустой текст"):
            deepseek.chat(
                [{"role": "user", "content": "hi"}],
                api_key="k", transport=fake_response(payload),
            )

    def test_suggest_menu_ideas_mentions_products(self):
        products = [Product(name="Куриное филе", quantity=1, unit="кг",
                            location="холодильник", expires_at=None)]
        reply = deepseek.suggest_menu_ideas(
            products, api_key="k", transport=fake_response(CHAT_OK))
        self.assertIn("Омлет", reply)

    def test_suggest_menu_ideas_without_products(self):
        self.assertIn("Нет продуктов", deepseek.suggest_menu_ideas([]))

    def test_suggest_menu_ideas_puts_allergens_into_prompt(self):
        """Регрессия: LLM не знала об ограничениях семьи, фильтр резал выдачу постфактум."""
        transport = RecordingTransport(CHAT_OK)
        products = [Product(name="Куриное филе", quantity=1, unit="кг",
                            location="холодильник", expires_at=None)]
        deepseek.suggest_menu_ideas(
            products, api_key="k", allergens=["арахис", " Глютен ", ""],
            transport=transport,
        )
        prompt = transport.prompt
        self.assertIn("СТРОГОЕ ОГРАНИЧЕНИЕ ПО ЗДОРОВЬЮ", prompt)
        self.assertIn("арахис, глютен", prompt)  # нормализовано и отсортировано
        self.assertIn("Куриное филе", prompt)
        self.assertNotIn("Глютен", prompt)

    def test_suggest_menu_ideas_without_allergens_has_no_restriction(self):
        transport = RecordingTransport(CHAT_OK)
        products = [Product(name="Рис", quantity=1, unit="кг",
                            location="шкаф", expires_at=None)]
        deepseek.suggest_menu_ideas(products, api_key="k", transport=transport)
        self.assertNotIn("ОГРАНИЧЕНИЕ", transport.prompt)

        deepseek.suggest_menu_ideas(products, api_key="k", allergens=[], transport=transport)
        self.assertNotIn("ОГРАНИЧЕНИЕ", transport.prompt)


if __name__ == "__main__":
    unittest.main()
