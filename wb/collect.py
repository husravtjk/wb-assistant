"""Сборщики: тянут данные из WB API и складывают в SQLite."""
import logging
from datetime import date, datetime, timedelta

from . import db
from .client import WBClient

log = logging.getLogger("wb.collect")


def _d(v, default=0):
    return v if v is not None else default


async def collect_orders(cli: WBClient, store: str, days: int = 30) -> int:
    date_from = (date.today() - timedelta(days=days)).isoformat()
    data = await cli.orders(date_from) or []
    rows = [{
        "store": store,
        "srid": o.get("srid") or f"{o.get('odid')}",
        "date": (o.get("date") or "")[:19],
        "last_change": (o.get("lastChangeDate") or "")[:19],
        "nm_id": o.get("nmId"),
        "article": o.get("supplierArticle"),
        "brand": o.get("brand"),
        "subject": o.get("subject"),
        "category": o.get("category"),
        "total_price": _d(o.get("totalPrice")),
        "discount_pct": _d(o.get("discountPercent")),
        "finished_price": _d(o.get("finishedPrice")),
        "warehouse": o.get("warehouseName"),
        "region": o.get("regionName") or o.get("oblastOkrugName"),
        "is_cancel": 1 if o.get("isCancel") else 0,
    } for o in data]
    n = db.upsert("orders", rows)
    db.log_run(store, "orders", "ok", f"{n} строк")
    return n


async def collect_sales(cli: WBClient, store: str, days: int = 30) -> int:
    date_from = (date.today() - timedelta(days=days)).isoformat()
    data = await cli.sales(date_from) or []
    rows = [{
        "store": store,
        "sale_id": s.get("saleID") or s.get("srid"),
        "date": (s.get("date") or "")[:19],
        "nm_id": s.get("nmId"),
        "article": s.get("supplierArticle"),
        "finished_price": _d(s.get("finishedPrice")),
        "for_pay": _d(s.get("forPay")),
        "price_with_disc": _d(s.get("priceWithDisc")),
        "warehouse": s.get("warehouseName"),
        "region": s.get("regionName"),
    } for s in data]
    n = db.upsert("sales", rows)
    db.log_run(store, "sales", "ok", f"{n} строк")
    return n


async def collect_stocks(cli: WBClient, store: str) -> int:
    data = await cli.stocks(date.today().isoformat()) or []
    snap = date.today().isoformat()
    rows = [{
        "store": store, "snap_date": snap,
        "nm_id": s.get("nmId"), "article": s.get("supplierArticle"),
        "barcode": s.get("barcode") or "", "warehouse": s.get("warehouseName") or "",
        "subject": s.get("subject"), "brand": s.get("brand"),
        "quantity": _d(s.get("quantity")),
        "in_way_to_client": _d(s.get("inWayToClient")),
        "in_way_from_client": _d(s.get("inWayFromClient")),
        "quantity_full": _d(s.get("quantityFull")),
        "price": _d(s.get("Price")), "discount": _d(s.get("Discount")),
    } for s in data]
    n = db.upsert("stocks", rows)
    db.log_run(store, "stocks", "ok", f"{n} строк")
    return n


async def collect_prices(cli: WBClient, store: str) -> int:
    rows, offset = [], 0
    while True:
        resp = await cli.goods_prices(limit=1000, offset=offset)
        goods = ((resp or {}).get("data") or {}).get("listGoods") or []
        if not goods:
            break
        for g in goods:
            sizes = g.get("sizes") or [{}]
            first = sizes[0]
            rows.append({
                "store": store, "nm_id": g.get("nmID"),
                "article": g.get("vendorCode"),
                "price": _d(first.get("price")),
                "discount": _d(g.get("discount")),
                "club_discount": _d(g.get("clubDiscount")),
                "updated": datetime.now().isoformat(timespec="seconds"),
            })
        offset += len(goods)
        if len(goods) < 1000:
            break
    n = db.upsert("prices", rows)
    db.log_run(store, "prices", "ok", f"{n} строк")
    return n


async def collect_adverts(cli: WBClient, store: str, days: int = 7) -> int:
    counts = await cli.adv_promotion_count() or {}
    ids: list[int] = []
    for grp in counts.get("adverts") or []:
        for a in grp.get("advert_list") or []:
            ids.append(a["advertId"])
    if not ids:
        db.log_run(store, "adverts", "ok", "нет кампаний")
        return 0

    info = await cli.adv_info(ids[:50]) or []
    camp_rows = [{
        "store": store, "advert_id": a.get("advertId"),
        "name": a.get("name"), "type": a.get("type"), "status": a.get("status"),
        "daily_budget": _d(a.get("dailyBudget")),
        "budget": None,
        "updated": datetime.now().isoformat(timespec="seconds"),
    } for a in info if a.get("advertId")]

    active = [c["advert_id"] for c in camp_rows if c["status"] in (9, 11)]
    for aid in active[:30]:
        try:
            b = await cli.adv_budget(aid)
            for c in camp_rows:
                if c["advert_id"] == aid:
                    c["budget"] = _d((b or {}).get("total"))
        except Exception as e:  # noqa: BLE001
            log.warning("бюджет кампании %s: %s", aid, e)
    db.upsert("adv_campaigns", camp_rows)

    dates = [(date.today() - timedelta(days=i)).isoformat() for i in range(days, 0, -1)]
    stat_rows = []
    try:
        stats = await cli.adv_fullstats(active[:30] or ids[:30], dates) or []
        for s in stats:
            aid = s.get("advertId")
            for d in s.get("days") or []:
                stat_rows.append({
                    "store": store, "date": (d.get("date") or "")[:10],
                    "advert_id": aid,
                    "views": _d(d.get("views")), "clicks": _d(d.get("clicks")),
                    "ctr": _d(d.get("ctr")), "cpc": _d(d.get("cpc")),
                    "spend": _d(d.get("sum")), "orders": _d(d.get("orders")),
                    "sum_price": _d(d.get("sum_price")),
                })
    except Exception as e:  # noqa: BLE001
        db.log_run(store, "adv_stats", "error", str(e))
        log.warning("[%s] статистика рекламы: %s", store, e)

    n = db.upsert("adv_stats", stat_rows)
    db.log_run(store, "adverts", "ok", f"{len(camp_rows)} кампаний, {n} дней статистики")
    return n


async def collect_feedbacks(cli: WBClient, store: str) -> int:
    rows = []
    for answered in (False, True):
        resp = await cli.feedbacks(is_answered=answered, take=100)
        for f in ((resp or {}).get("data") or {}).get("feedbacks") or []:
            pd = f.get("productDetails") or {}
            rows.append({
                "store": store, "fb_id": f.get("id"),
                "date": (f.get("createdDate") or "")[:19],
                "nm_id": pd.get("nmId"), "article": pd.get("supplierArticle"),
                "rating": _d(f.get("productValuation")),
                "text": (f.get("text") or "")[:2000],
                "answered": 1 if answered else 0,
                "notified": 0,
            })
    existing = {r["fb_id"] for r in db.query(
        "SELECT fb_id FROM feedbacks WHERE store=?", (store,))}
    for r in rows:
        if r["fb_id"] in existing:
            r.pop("notified")
    n = db.upsert("feedbacks", rows)
    db.log_run(store, "feedbacks", "ok", f"{n} отзывов")
    return n


async def collect_questions(cli: WBClient, store: str) -> int:
    resp = await cli.questions(is_answered=False, take=100)
    rows = [{
        "store": store, "q_id": q.get("id"),
        "date": (q.get("createdDate") or "")[:19],
        "nm_id": (q.get("productDetails") or {}).get("nmId"),
        "text": (q.get("text") or "")[:2000],
        "answered": 0, "notified": 0,
    } for q in ((resp or {}).get("data") or {}).get("questions") or []]
    n = db.upsert("questions", rows)
    db.log_run(store, "questions", "ok", f"{n} вопросов")
    return n


ALL_TASKS = {
    "orders": collect_orders,
    "sales": collect_sales,
    "stocks": collect_stocks,
    "prices": collect_prices,
    "adverts": collect_adverts,
    "feedbacks": collect_feedbacks,
    "questions": collect_questions,
}


async def collect_store(cli: WBClient, store: str, tasks: list[str] | None = None) -> dict:
    result = {}
    for name in (tasks or list(ALL_TASKS)):
        fn = ALL_TASKS.get(name)
        if not fn:
            continue
        try:
            result[name] = await fn(cli, store)
        except Exception as e:  # noqa: BLE001
            log.error("[%s] задача %s упала: %s", store, name, e)
            db.log_run(store, name, "error", str(e))
            result[name] = f"ошибка: {e}"
    return result
