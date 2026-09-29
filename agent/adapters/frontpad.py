"""Адаптер товаров FrontPad → инвентарь SmartKitchen Family.

FrontPad — облачная POS/CRM для кафе и ресторанов, популярная у небольших
заведений. Адаптер читает справочник товаров через API FrontPad
(POST-запрос с параметром «команда»). Конкретный набор команд
и полей зависит от тарифа и документации FrontPad — они вынесены в константы.

Подключение (секреты — только из окружения):
- FRONTPAD_BASE_URL — например https://<ваш-домен>.frontpad.ru
- FRONTPAD_SECRET — секретный ключ API из личного кабинета FrontPad

Волна 5 (п.14): секрет отправляется ровно одним каналом — заголовком
``Authorization: Bearer``; в теле формы он больше не дублируется (раньше
логи прокси/веб-сервера могли записать его дважды: и в заголовке, и в теле).

Ограничения заготовки: только чтение; остатки и сроки годности не импортируются
(quantity = 0, позиции уходят в «ignored» до ручного подтверждения).
Модуль — тонкая конфигурация общей фабрики ``make_rest_source`` (base.py).
"""
from __future__ import annotations

from agent.adapters.base import make_rest_source

SYSTEM = "frontpad"

# Команда API и имена полей (проверить по документации FrontPad для вашего тарифа).
COMMAND = "getProducts"
NAME_FIELD = "name"
UNIT_FIELD = "unit"

import_products = make_rest_source(
    "FrontPad",
    base_url_env="FRONTPAD_BASE_URL",
    token_env="FRONTPAD_SECRET",
    endpoint="/api/",
    body_kind="form-command",
    command=COMMAND,
    location="FrontPad (справочник)",
)
