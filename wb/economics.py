"""Юнит-экономика: точка безубыточности и проверка текущих цен.

Модель считает на одну проданную (выкупленную) единицу. Логистика размазывается
с учётом процента выкупа: за невыкуп WB всё равно берёт доставку туда и обратно,
и этот расход ложится на те единицы, которые выкупили.
"""
import csv
import os
from datetime import date, timedelta

from . import db
from .config import ROOT

COSTS_CSV = os.path.join(ROOT, "costs.csv")


def load_costs_csv(store_filter: str | None = None) -> int:
    """Читает costs.csv и кладёт в базу. Колонки: store,nm_id,article,cost,
    packaging,delivery_to_wh,other. Пустые числовые поля считаются нулями."""
    if not os.path.exists(COSTS_CSV):
        return 0
    rows = []
    with open(COSTS_CSV, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not r.get("nm_id"):
                continue
            if store_filter and r.get("store") != store_filter:
                continue
            def num(key):
                v = (r.get(key) or "").strip().replace(",", ".")
                return float(v) if v else 0.0
            rows.append({
                "store": r["store"].strip(),
                "nm_id": int(r["nm_id"]),
                "article": (r.get("article") or "").strip(),
                "cost": num("cost"),
                "packaging": num("packaging"),
                "delivery_to_wh": num("delivery_to_wh"),
                "other": num("other"),
            })
    return db.upsert("costs", rows)


def redemption_rate(store: str, days: int = 30, nm_id: int | None = None) -> float:
    """Доля выкупа: выкупы ÷ заказы за период. Возвращает 0..1."""
    since = (date.today() - timedelta(days=days)).isoformat()
    args = [store, since]
    nm_clause = ""
    if nm_id:
        nm_clause = " AND nm_id=?"
        args.append(nm_id)

    orders = db.query(
        f"SELECT COUNT(*) c FROM orders WHERE store=? AND date>=? AND is_cancel=0{nm_clause}",
        tuple(args),
    )[0]["c"]
    buys = db.query(
        f"SELECT COUNT(*) c FROM sales WHERE store=? AND date>=? AND is_return=0{nm_clause}",
        tuple(args),
    )[0]["c"]
    if not orders:
        return 0.0
    return min(1.0, buys / orders)


def break_even(cost_row: dict, econ: dict, redemption: float, ad_per_order: float = 0.0) -> dict:
    """Минимальная цена, ниже которой товар уходит в минус."""
    commission = econ.get("commission_pct", 0.20)
    acquiring = econ.get("acquiring_pct", 0.02)
    tax = econ.get("tax_pct", 0.06)
    logistics = econ.get("logistics_rub", 60.0)
    return_logistics = econ.get("return_logistics_rub", 50.0)
    storage = econ.get("storage_rub", 5.0)

    r = max(0.05, redemption or econ.get("default_redemption", 0.7))
    logistics_eff = logistics / r + return_logistics * (1 - r) / r

    fixed = (
        cost_row.get("cost", 0)
        + cost_row.get("packaging", 0)
        + cost_row.get("delivery_to_wh", 0)
        + cost_row.get("other", 0)
        + storage
        + logistics_eff
        + ad_per_order
    )
    share = 1 - commission - acquiring - tax
    if share <= 0:
        return {"min_price": None, "error": "комиссия+налог+эквайринг ≥ 100%"}
    return {
        "min_price": fixed / share,
        "fixed": fixed,
        "logistics_eff": logistics_eff,
        "redemption": r,
        "share": share,
    }


def profit_at(price: float, cost_row: dict, econ: dict, redemption: float,
              ad_per_order: float = 0.0) -> float:
    be = break_even(cost_row, econ, redemption, ad_per_order)
    if be.get("min_price") is None:
        return 0.0
    return price * be["share"] - be["fixed"]


def ad_cost_per_order(store: str, days: int = 7) -> float:
    since = (date.today() - timedelta(days=days)).isoformat()
    row = db.query(
        "SELECT COALESCE(SUM(spend),0) s, COALESCE(SUM(orders),0) o "
        "FROM adv_stats WHERE store=? AND date>=?",
        (store, since),
    )[0]
    return (row["s"] / row["o"]) if row["o"] else 0.0


def margin_report(store: str, econ: dict) -> list[dict]:
    """По каждому SKU с известной себестоимостью: текущая цена против минимальной."""
    rows = db.query(
        """
        SELECT c.nm_id, c.article, c.cost, c.packaging, c.delivery_to_wh, c.other,
               p.price, p.discount
        FROM costs c LEFT JOIN prices p ON p.store=c.store AND p.nm_id=c.nm_id
        WHERE c.store=?
        """,
        (store,),
    )
    ad = ad_cost_per_order(store)
    out = []
    for r in rows:
        if r["price"] is None:
            continue
        final_price = r["price"] * (1 - (r["discount"] or 0) / 100)
        red = redemption_rate(store, nm_id=r["nm_id"]) or redemption_rate(store)
        be = break_even(dict(r), econ, red, ad)
        if be.get("min_price") is None:
            continue
        profit = final_price * be["share"] - be["fixed"]
        out.append({
            "nm_id": r["nm_id"], "article": r["article"],
            "price": final_price, "min_price": be["min_price"],
            "profit": profit,
            "margin_pct": (profit / final_price * 100) if final_price else 0,
            "redemption": be["redemption"],
            "ad_per_order": ad,
            "loss": profit < 0,
        })
    return sorted(out, key=lambda x: x["profit"])
