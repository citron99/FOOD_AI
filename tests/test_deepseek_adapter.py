import json
import os
import unittest
from unittest import mock

from agent.adapters import deepseek
from agent.adapters.base import AdapterError, AdapterResponseError
from agent.cli import Product


def fake_response(payload):
    raw = json.dumps(payload).encode("utf-8")
    return lambda url, body, headers, timeout: raw


class RecordingTransport:
    """Транспорт, запоминающий запрос: URL, тело и таймаут — для проверки
    содержимого промпта и фактических параметров подключения."""

    def __init__(self, payload):
        self.payload = payload
        self.url = None
        self.request = None
        self.timeout = None

    def __call__(self, url, body, headers, timeout):
        self.url = url
        self.request = json.loads(body.decode("utf-8"))
        self.timeout = timeout
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


class DeepSeekEnvConfigTests(unittest.TestCase):
    """П.7–П.8 ревью: BASE_URL/MODEL/TIMEOUT читаются из окружения,
    явный аргумент важнее env, опечатка в TIMEOUT — ошибка, а не игнор."""

    def setUp(self):
        # Изолируемся от реального окружения разработчика/CI.
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def chat(self, transport=None, **kwargs):
        transport = transport or RecordingTransport(CHAT_OK)
        deepseek.chat([{"role": "user", "content": "hi"}],
                      api_key="k", transport=transport, **kwargs)
        return transport

    def test_defaults_without_env(self):
        transport = self.chat()
        self.assertEqual(transport.url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(transport.request["model"], "deepseek-chat")
        self.assertEqual(transport.timeout, 30.0)

    def test_env_base_url_and_model_are_used(self):
        os.environ["DEEPSEEK_BASE_URL"] = "https://proxy.example.com"
        os.environ["DEEPSEEK_MODEL"] = "deepseek-reasoner"
        transport = self.chat()
        self.assertEqual(transport.url, "https://proxy.example.com/chat/completions")
        self.assertEqual(transport.request["model"], "deepseek-reasoner")

    def test_explicit_args_beat_env(self):
        os.environ["DEEPSEEK_BASE_URL"] = "https://proxy.example.com"
        os.environ["DEEPSEEK_MODEL"] = "deepseek-reasoner"
        transport = self.chat(base_url="https://explicit.example.com",
                              model="explicit-model")
        self.assertEqual(transport.url, "https://explicit.example.com/chat/completions")
        self.assertEqual(transport.request["model"], "explicit-model")

    def test_timeout_from_env(self):
        os.environ["DEEPSEEK_TIMEOUT"] = "45"
        self.assertEqual(self.chat().timeout, 45.0)

    def test_explicit_timeout_beats_env(self):
        os.environ["DEEPSEEK_TIMEOUT"] = "45"
        self.assertEqual(self.chat(timeout=7.5).timeout, 7.5)

    def test_non_numeric_timeout_is_an_error(self):
        os.environ["DEEPSEEK_TIMEOUT"] = "abc"
        with self.assertRaisesRegex(AdapterError, "DEEPSEEK_TIMEOUT"):
            self.chat()

    def test_non_positive_timeout_is_an_error(self):
        os.environ["DEEPSEEK_TIMEOUT"] = "0"
        with self.assertRaisesRegex(AdapterError, "DEEPSEEK_TIMEOUT"):
            self.chat()


if __name__ == "__main__":
    unittest.main()
