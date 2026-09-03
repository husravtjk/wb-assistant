"""Запуск: python main.py  — поднимает сервер и планировщик на ноутбуке."""
import asyncio
import logging
import sys

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from wb import analytics, config, db
from wb.client import Throttle, WBClient
from wb.collect import collect_store
from wb.notify import send
from wb.web import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("wb")

cfg = config.load()
db.init()
throttle = Throttle(cfg.rate_limits)


def client(store) -> WBClient:
    return WBClient(store.token, cfg.hosts, throttle, store.key)


async def job_collect(tasks: list[str] | None = None) -> None:
    for st in cfg.active_stores:
        log.info("Сбор: %s", st.name)
        await collect_store(client(st), st.key, tasks)


async def job_digest() -> None:
    await job_collect()
    text = analytics.build_digest(cfg.active_stores, cfg.thresholds)
    await send(cfg.telegram.get("bot_token"), cfg.telegram.get("chat_id"), text)
    log.info("Сводка отправлена")


def setup_scheduler() -> AsyncIOScheduler:
    tz = cfg.schedule.get("timezone", "Europe/Moscow")
    sched = AsyncIOScheduler(timezone=tz)

    hh, mm = (cfg.schedule.get("digest_time", "09:00").split(":") + ["0"])[:2]
    sched.add_job(job_digest, CronTrigger(hour=int(hh), minute=int(mm)),
                  id="digest", misfire_grace_time=3600)

    every = int(cfg.schedule.get("collect_every_min", 60))
    sched.add_job(job_collect, IntervalTrigger(minutes=every),
                  args=[["orders", "sales", "feedbacks", "questions"]],
                  id="collect", misfire_grace_time=1800)

    sched.add_job(job_collect, CronTrigger(hour="*/6"),
                  args=[["stocks", "prices", "adverts"]],
                  id="heavy", misfire_grace_time=1800)
    return sched


async def cli_once(what: str) -> None:
    if what == "collect":
        await job_collect()
    elif what == "digest":
        print(analytics.build_digest(cfg.active_stores, cfg.thresholds))
    elif what == "send":
        await job_digest()
    elif what == "check":
        for st in cfg.active_stores:
            try:
                await client(st).ping()
                info = await client(st).seller_info()
                print(f"{st.name}: ok — {info}")
            except Exception as e:  # noqa: BLE001
                print(f"{st.name}: ОШИБКА — {e}")
    else:
        print("Команды: collect | digest | send | check")


def main() -> None:
    if len(sys.argv) > 1:
        asyncio.run(cli_once(sys.argv[1]))
        return

    app = create_app(cfg)

    @app.on_event("startup")
    async def _start():
        sched = setup_scheduler()
        sched.start()
        log.info("Планировщик запущен, задач: %s", len(sched.get_jobs()))

    host = cfg.server.get("host", "127.0.0.1")
    port = int(cfg.server.get("port", 8765))
    log.info("Панель: http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
