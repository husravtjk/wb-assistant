"""Клиент WB Seller API: троттлинг по хостам, ретраи на 429/5xx."""
import asyncio
import logging
import time
from typing import Any

import httpx

log = logging.getLogger("wb.client")


class Throttle:
    """Простой лимитер: не чаще N запросов в минуту на ключ (хост+путь)."""

    def __init__(self, rate_limits: dict):
        self.rate_limits = rate_limits
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def wait(self, host_key: str, path: str) -> None:
        rpm = max(1, int(self.rate_limits.get(host_key, 30)))
        interval = 60.0 / rpm
        key = f"{host_key}:{path}"
        async with self._lock(key):
            prev = self._last.get(key, 0.0)
            delta = time.monotonic() - prev
            if delta < interval:
                await asyncio.sleep(interval - delta)
            self._last[key] = time.monotonic()


class WBClient:
    def __init__(self, token: str, hosts: dict, throttle: Throttle, store_key: str = ""):
        self.token = token
        self.hosts = hosts
        self.throttle = throttle
        self.store_key = store_key

    async def request(
        self,
        host_key: str,
        path: str,
        method: str = "GET",
        params: dict | None = None,
        json_body: Any = None,
        retries: int = 3,
        timeout: float = 60.0,
    ) -> Any:
        base = self.hosts.get(host_key)
        if not base:
            raise ValueError(f"Не задан хост '{host_key}' в config.yaml")
        url = base.rstrip("/") + path
        headers = {"Authorization": self.token, "Content-Type": "application/json"}

        for attempt in range(1, retries + 1):
            await self.throttle.wait(host_key, path)
            try:
                async with httpx.AsyncClient(timeout=timeout) as c:
                    r = await c.request(
                        method, url, headers=headers, params=params, json=json_body
                    )
            except httpx.RequestError as e:
                log.warning("[%s] сеть %s %s: %s", self.store_key, method, path, e)
                if attempt == retries:
                    raise
                await asyncio.sleep(5 * attempt)
                continue

            if r.status_code == 429:
                wait = min(120, 20 * attempt)
                log.warning("[%s] 429 на %s, жду %sс", self.store_key, path, wait)
                await asyncio.sleep(wait)
                continue

            if r.status_code == 401:
                raise PermissionError(
                    f"[{self.store_key}] 401 на {path}: токен невалиден или без нужной категории"
                )

            if r.status_code >= 500:
                if attempt == retries:
                    raise RuntimeError(f"[{self.store_key}] {r.status_code} на {path}")
                await asyncio.sleep(5 * attempt)
                continue

            if r.status_code == 404:
                raise FileNotFoundError(f"[{self.store_key}] 404 {path} — метод переехал")

            if r.status_code >= 400:
                raise RuntimeError(
                    f"[{self.store_key}] {r.status_code} на {path}: {r.text[:300]}"
                )

            if not r.content:
                return None
            try:
                return r.json()
            except ValueError:
                return r.text

        raise RuntimeError(f"[{self.store_key}] не удалось выполнить {path}")

    # --- Готовые методы под конкретные задачи -------------------------------

    async def ping(self):
        return await self.request("common", "/ping")

    async def seller_info(self):
        return await self.request("common", "/api/v1/seller-info")

    async def orders(self, date_from: str, flag: int = 0):
        return await self.request(
            "statistics", "/api/v1/supplier/orders",
            params={"dateFrom": date_from, "flag": flag},
        )

    async def sales(self, date_from: str, flag: int = 0):
        return await self.request(
            "statistics", "/api/v1/supplier/sales",
            params={"dateFrom": date_from, "flag": flag},
        )

    async def stocks(self, date_from: str):
        return await self.request(
            "statistics", "/api/v1/supplier/stocks", params={"dateFrom": date_from}
        )

    async def cards(self, limit: int = 100, cursor: dict | None = None):
        body = {
            "settings": {
                "cursor": {"limit": limit, **(cursor or {})},
                "filter": {"withPhoto": -1},
            }
        }
        return await self.request(
            "content", "/content/v2/get/cards/list", method="POST", json_body=body
        )

    async def goods_prices(self, limit: int = 1000, offset: int = 0):
        return await self.request(
            "prices", "/api/v2/list/goods/filter",
            params={"limit": limit, "offset": offset},
        )

    async def adv_balance(self):
        return await self.request("advert", "/adv/v1/balance")

    async def adv_promotion_count(self):
        return await self.request("advert", "/adv/v1/promotion/count")

    async def adv_info(self, advert_ids: list[int]):
        """Информация о кампаниях. Пробуем v2, при 404 откатываемся на v1."""
        try:
            return await self.request(
                "advert", "/api/advert/v2/adverts",
                method="POST", json_body=advert_ids,
            )
        except (FileNotFoundError, RuntimeError):
            return await self.request(
                "advert", "/adv/v1/adverts", method="POST", json_body=advert_ids
            )

    async def adv_budget(self, advert_id: int):
        return await self.request("advert", "/adv/v1/budget", params={"id": advert_id})

    async def adv_fullstats(self, advert_ids: list[int], dates: list[str]):
        """Статистика кампаний. WB менял версию — пробуем v3 (GET), потом v2 (POST)."""
        try:
            return await self.request(
                "advert", "/adv/v3/fullstats",
                params={"id": ",".join(map(str, advert_ids)), "dates": ",".join(dates)},
            )
        except (FileNotFoundError, RuntimeError):
            body = [{"id": i, "dates": dates} for i in advert_ids]
            return await self.request(
                "advert", "/adv/v2/fullstats", method="POST", json_body=body
            )

    async def feedbacks(self, is_answered: bool, take: int = 100, skip: int = 0):
        return await self.request(
            "feedbacks", "/api/v1/feedbacks",
            params={"isAnswered": str(is_answered).lower(), "take": take, "skip": skip,
                    "order": "dateDesc"},
        )

    async def feedback_answer(self, feedback_id: str, text: str):
        return await self.request(
            "feedbacks", "/api/v1/feedbacks/answer", method="POST",
            json_body={"id": feedback_id, "text": text},
        )

    async def questions(self, is_answered: bool, take: int = 100, skip: int = 0):
        return await self.request(
            "feedbacks", "/api/v1/questions",
            params={"isAnswered": str(is_answered).lower(), "take": take, "skip": skip,
                    "order": "dateDesc"},
        )

    async def seller_rating(self):
        return await self.request("common", "/api/common/v1/rating")
