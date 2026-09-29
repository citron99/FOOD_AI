"""Общая база для адаптеров внешних POS/учётных систем.

Все адаптеры следуют одним правилам (согласовано с cli_agent_design.md и ТЗ):
- секреты читаются только из окружения, никогда из CLI-флагов и репозитория;
- адаптер только читает справочник/номенклатуру, запись во внешнюю систему
  не выполняется;
- остатки и сроки годности не выдумываются: импортированные позиции получают
  quantity = 0 и попадают в «ignored» до ручного подтверждения пользователем;
- транспорт внедряется (dependency injection) — адаптеры тестируются без сети.

Волна 5 (ревью, п.11/14/15/16): ряд однотипных REST-адаптеров собран
фабрикой ``make_rest_source``; схема авторизации (Bearer/Basic/нет) задаётся
полем ``auth_kind`` конфигурации, а не кодируется вручную в каждом модуле;
маппинг полей зависит от переданного конфига, а не от глобального окружения;
отсутствие переменных окружения — это ``AdapterAuthError`` (проблема
конфигурации доступа), а не ``AdapterResponseError`` (плохой ответ системы).
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent.models import Product

Transport = Callable[[str, bytes | None, dict[str, str], float], bytes]


class AdapterError(Exception):
    """Базовая ошибка адаптера с понятным сообщением для пользователя."""


class AdapterAuthError(AdapterError):
    """Ошибка аутентификации или её конфигурации (токен/логин/пароль)."""


class AdapterResponseError(AdapterError):
    """Некорректный, пустой или неожиданный ответ внешней системы."""


def http_transport(url: str, body: bytes | None, headers: dict[str, str], timeout: float) -> bytes:
    """Транспорт по умолчанию: один HTTP-запрос через стандартную urllib."""
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AdapterAuthError(
                f"Внешняя система отклонила учётные данные ({url}, HTTP {exc.code})"
            ) from None
        raise AdapterError(f"Внешняя система вернула HTTP {exc.code} для {url}") from None
    except urllib.error.URLError as exc:
        raise AdapterError(f"Не удалось подключиться к внешней системе ({url}): {exc.reason}") from None


def basic_authorization(credentials: str) -> str:
    """Кодирует строку учётных данных «пользователь:пароль» в заголовок Basic."""
    return "Basic " + base64.b64encode(credentials.encode("utf-8")).decode("ascii")


@dataclass(frozen=True)
class RestConfig:
    """Параметры REST-подключения; секреты — только из окружения."""

    base_url: str
    endpoint: str
    token: str = ""
    username: str = ""
    password: str = ""
    timeout: float = 10.0
    # Схема авторизации: "bearer" (токен в заголовке), "basic" (логин:пароль)
    # или "none". Волна 5 (п.14): раньше Basic собирался вручную в адаптерах.
    auth_kind: str = "bearer"
    # Имена полей наименования и единицы измерения в ответе системы.
    # Пустая строка означает «не задано» — фабрика make_rest_source подставит
    # системные имена из своей конфигурации (волна 5, п.15: маппинг читает
    # их из конфига, а не из os.environ).
    name_field: str = ""
    unit_field: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.endpoint.lstrip("/")

    def auth_headers(self) -> dict[str, str]:
        if self.auth_kind == "basic":
            if self.username or self.password:
                return {"Authorization": basic_authorization(f"{self.username}:{self.password}")}
            return {}
        if self.auth_kind == "bearer" and self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}


def fetch_json(config: RestConfig, transport: Transport = http_transport,
               body: bytes | None = None, headers: dict[str, str] | None = None) -> Any:
    """Выполняет запрос и разбирает JSON-ответ с понятными ошибками."""
    all_headers = {"Accept": "application/json", **config.auth_headers(), **(headers or {})}
    raw = transport(config.url, body, all_headers, config.timeout)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterResponseError(f"Внешняя система вернула некорректный JSON: {exc}") from None
    return payload


@dataclass
class ImportOutcome:
    """Результат импорта: позиции и статистика пропусков."""

    products: list[Product]
    total: int
    skipped: int  # позиции, не прошедшие маппинг (без наименования и т.п.)

    @property
    def imported(self) -> int:
        return len(self.products)


def build_outcome(items: list[Any], map_one: Callable[[Any], Product]) -> ImportOutcome:
    """Маппит список сырых элементов, считая пропущенные позиции."""
    products: list[Product] = []
    skipped = 0
    for item in items:
        try:
            products.append(map_one(item))
        except AdapterResponseError:
            skipped += 1
    return ImportOutcome(products=products, total=len(items), skipped=skipped)


def make_rest_source(
    system: str,
    *,
    base_url_env: str,
    endpoint: str,
    location: str,
    token_env: str | None = None,
    username_env: str | None = None,
    password_env: str | None = None,
    endpoint_env: str | None = None,
    default_base_url: str = "",
    auth_kind: str = "bearer",
    body_kind: str | None = None,
    command: str = "",
    items_key: str | None = None,
    unit_nested: bool = False,
    name_field: str = "name",
    unit_field: str = "unit",
    name_field_env: str | None = None,
    unit_field_env: str | None = None,
) -> Callable[..., ImportOutcome]:
    """Собирает импортёр справочника из параметров подключения (волна 5, п.11).

    Одна фабрика вместо пяти почти идентичных модулей: ``iiko``, ``quickresto``,
    ``frontpad``, ``saby`` и ``yuma`` отличаются только набором параметров
    ниже. Возвращает функцию ``import_products(config=None, transport=...)``
    с тем же контрактом, что раньше реализовывался вручную.

    Параметры:
    - ``system`` — имя системы в сообщениях об ошибках;
    - ``base_url_env`` / ``token_env`` / ``username_env`` / ``password_env`` —
      переменные окружения с параметрами подключения (секреты — только там);
    - ``endpoint`` (+ ``endpoint_env`` для переопределения из окружения);
    - ``auth_kind`` — «bearer» | «basic» | «none» (см. ``RestConfig``);
    - ``body_kind`` — None (GET), «json-empty» (POST ``{}``),
      «form-command» (POST ``cmd=<command>``);
    - ``items_key`` — ключ списка позиций в объектном ответе (None — ответ
      сам является списком);
    - ``unit_nested`` — единица измерения приходит объектом с полем «name»
      (iiko ``mainUnit``);
    - ``name_field`` / ``unit_field`` — фиксированные имена полей;
      ``name_field_env`` / ``unit_field_env`` — имена полей из окружения
      (читаются один раз при построении конфигурации, п.15).

    Секрет отправляется ровно одним каналом — заголовком Authorization
    (п.14): тело формы и другие поверхности утечки не используются.
    """
    if auth_kind not in ("bearer", "basic", "none"):
        raise ValueError(f"{system}: неизвестный auth_kind {auth_kind!r} (нужен bearer|basic|none)")
    if body_kind not in (None, "json-empty", "form-command"):
        raise ValueError(f"{system}: неизвестный body_kind {body_kind!r}")

    def _config_from_env() -> RestConfig:
        base_url = os.environ.get(base_url_env, default_base_url).rstrip("/")
        token = os.environ.get(token_env, "") if token_env else ""
        username = os.environ.get(username_env, "") if username_env else ""
        password = os.environ.get(password_env, "") if password_env else ""
        required: list[tuple[str, str]] = [(base_url_env, base_url)]
        if auth_kind == "bearer" and token_env:
            required.append((token_env, token))
        if auth_kind == "basic":
            required.extend((
                (username_env or "", username),
                (password_env or "", password),
            ))
        missing = [env for env, value in required if env and not value]
        if missing:
            raise AdapterAuthError(
                f"Не заданы переменные окружения для {system}: " + ", ".join(missing)
            )
        resolved_endpoint = os.environ.get(endpoint_env, endpoint) if endpoint_env else endpoint
        resolved_name_field = (
            os.environ.get(name_field_env, name_field) if name_field_env else name_field
        )
        resolved_unit_field = (
            os.environ.get(unit_field_env, unit_field) if unit_field_env else unit_field
        )
        return RestConfig(
            base_url=base_url,
            endpoint=resolved_endpoint,
            token=token,
            username=username,
            password=password,
            auth_kind=auth_kind,
            name_field=resolved_name_field,
            unit_field=resolved_unit_field,
        )

    def _map_product(item: dict, cfg: RestConfig) -> Product:
        # Поля из явно переданного конфига имеют приоритет; пустые значения
        # заменяются системными именами фабрики.
        name_key = cfg.name_field or name_field
        unit_key = cfg.unit_field or unit_field
        name = str(item.get(name_key, "")).strip()
        if not name:
            raise AdapterResponseError(f"Элемент справочника {system} без наименования: {item}")
        raw_unit = item.get(unit_key)
        if unit_nested and isinstance(raw_unit, dict):
            unit = str(raw_unit.get("name", ""))
        else:
            unit = str(raw_unit) if raw_unit else ""
        return Product(
            name=name,
            quantity=0.0,
            unit=unit or "шт.",
            location=location,
            expires_at=None,
            status="active",
        )

    def _request_body() -> tuple[bytes | None, dict[str, str]]:
        if body_kind == "json-empty":
            return json.dumps({}).encode("utf-8"), {"Content-Type": "application/json"}
        if body_kind == "form-command":
            params = urllib.parse.urlencode({"cmd": command}).encode("utf-8")
            return params, {"Content-Type": "application/x-www-form-urlencoded"}
        return None, {}

    def _extract_items(payload: Any) -> list[Any]:
        if items_key is not None:
            if not isinstance(payload, dict) or items_key not in payload:
                raise AdapterResponseError(f"{system} вернул неожиданную структуру: {payload}")
            items = payload[items_key]
            if not isinstance(items, list):
                raise AdapterResponseError(
                    f"Поле «{items_key}» в ответе {system} должно быть списком"
                )
            return items
        if not isinstance(payload, list):
            raise AdapterResponseError(f"{system} вернул неожиданную структуру: {payload}")
        return payload

    def import_products(config: RestConfig | None = None,
                        transport: Transport = http_transport) -> ImportOutcome:
        cfg = config or _config_from_env()
        body, headers = _request_body()
        payload = fetch_json(cfg, transport, body=body, headers=headers or None)
        return build_outcome(_extract_items(payload), lambda item: _map_product(item, cfg))

    return import_products
