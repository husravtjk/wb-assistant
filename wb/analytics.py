"""Расчёты поверх собранных данных."""
from datetime import date, timedelta

from . import db


def _fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def sales_summary(store: str, day: str) -> dict:
    row = db.query(
        "SELECT COUNT(*) c, COALESCE(SUM(finished_price),0) s "
        "FROM orders WHERE store=? AND date LIKE ? AND is_cancel=0",
        (store, f"{day}%"),
    )[0]
    prev = db.query(
        "SELECT COUNT(*) c, COALESCE(SUM(finished_price),0) s FROM orders "
        "WHERE store=? AND date >= ? AND date < ? AND is_cancel=0",
        (store, (date.fromisoformat(day) - timedelta(days=7)).isoformat(), day),
    )[0]
    avg_c = prev["c"] / 7 if prev["c"] else 0
    return {
        "orders": row["c"],
        "revenue": row["s"],
        "avg_check": row["s"] / row["c"] if row["c"] else 0,
        "avg_7d": avg_c,
        "delta_pct": ((row["c"] - avg_c) / avg_c * 100) if avg_c else 0,
    }


def oos_risk(store: str, days_threshold: int = 7) -> list[dict]:
    """SKU, которым остатка хватит меньше чем на N дней при текущем темпе."""
    snap = db.query(
        "SELECT MAX(snap_date) d FROM stocks WHERE store=?", (store,)
    )[0]["d"]
    if not snap:
        return []
    since = (date.today() - timedelta(days=14)).isoformat()
    rows = db.query(
        """
        SELECT s.nm_id, MIN(s.article) article, MIN(s.subject) subject,
               SUM(s.quantity) qty,
               (SELECT COUNT(*) FROM orders o
                 WHERE o.store=s.store AND o.nm_id=s.nm_id
                   AND o.date >= ? AND o.is_cancel=0) ord14
        FROM stocks s
        WHERE s.store=? AND s.snap_date=?
        GROUP BY s.nm_id
        """,
        (since, store, snap),
    )
    out = []
    for r in rows:
        per_day = r["ord14"] / 14
        if per_day <= 0:
            continue
        left = r["qty"] / per_day
        if left < days_threshold:
            out.append({
                "nm_id": r["nm_id"], "article": r["article"], "subject": r["subject"],
                "qty": r["qty"], "per_day": round(per_day, 1),
                "days_left": round(left, 1),
            })
    return sorted(out, key=lambda x: x["days_left"])


def demand_drop(store: str, drop_pct: int = 30) -> list[dict]:
    """SKU, у которых вчерашние заказы упали к среднему за 7 дней."""
    y = yesterday()
    since = (date.fromisoformat(y) - timedelta(days=7)).isoformat()
    rows = db.query(
        """
        SELECT nm_id, MIN(article) article,
               SUM(CASE WHEN date LIKE ? THEN 1 ELSE 0 END) yday,
               SUM(CASE WHEN date >= ? AND date < ? THEN 1 ELSE 0 END) prev7
        FROM orders WHERE store=? AND is_cancel=0 AND date >= ?
        GROUP BY nm_id
        """,
        (f"{y}%", since, y, store, since),
    )
    out = []
    for r in rows:
        base = r["prev7"] / 7
        if base < 1:
            continue
        drop = (base - r["yday"]) / base * 100
        if drop >= drop_pct:
            out.append({
                "nm_id": r["nm_id"], "article": r["article"],
                "yday": r["yday"], "avg7": round(base, 1), "drop": round(drop),
            })
    return sorted(out, key=lambda x: -x["drop"])


def adv_efficiency(store: str, day: str, cpo_limit: float = 300) -> list[dict]:
    rows = db.query(
        """
        SELECT a.advert_id, c.name, c.status, c.budget, c.daily_budget,
               a.spend, a.orders, a.clicks, a.views, a.ctr, a.cpc
        FROM adv_stats a LEFT JOIN adv_campaigns c
          ON c.store=a.store AND c.advert_id=a.advert_id
        WHERE a.store=? AND a.date=?
        """,
        (store, day),
    )
    out = []
    for r in rows:
        if r["spend"] <= 0:
            continue
        cpo = r["spend"] / r["orders"] if r["orders"] else None
        days_left = (r["budget"] / r["spend"]) if (r["budget"] and r["spend"]) else None
        out.append({
            "advert_id": r["advert_id"], "name": r["name"] or f"#{r['advert_id']}",
            "spend": r["spend"], "orders": r["orders"], "clicks": r["clicks"],
            "ctr": r["ctr"], "cpc": r["cpc"], "cpo": cpo,
            "budget": r["budget"],
            "budget_days": round(days_left, 1) if days_left else None,
            "flag": (cpo is None or cpo > cpo_limit),
        })
    return sorted(out, key=lambda x: -x["spend"])


def new_bad_feedbacks(store: str, max_rating: int = 3) -> list[dict]:
    rows = db.query(
        "SELECT fb_id, nm_id, article, rating, text, date FROM feedbacks "
        "WHERE store=? AND rating<=? AND notified=0 AND answered=0 "
        "ORDER BY date DESC LIMIT 15",
        (store, max_rating),
    )
    return [dict(r) for r in rows]


def mark_feedbacks_notified(store: str, ids: list[str]) -> None:
    if not ids:
        return
    with db.connect() as c:
        c.executemany(
            "UPDATE feedbacks SET notified=1 WHERE store=? AND fb_id=?",
            [(store, i) for i in ids],
        )


def unanswered_counts(store: str) -> dict:
    fb = db.query(
        "SELECT COUNT(*) c FROM feedbacks WHERE store=? AND answered=0", (store,)
    )[0]["c"]
    q = db.query(
        "SELECT COUNT(*) c FROM questions WHERE store=? AND answered=0", (store,)
    )[0]["c"]
    return {"feedbacks": fb, "questions": q}


def build_digest(stores: list, thresholds: dict) -> str:
    day = yesterday()
    t = thresholds
    parts = [f"<b>Сводка за {day}</b>"]

    for st in stores:
        s = sales_summary(st.key, day)
        arrow = "▲" if s["delta_pct"] >= 0 else "▼"
        parts.append(
            f"\n<b>{st.name}</b>\n"
            f"Заказы: {s['orders']} шт ({arrow}{abs(s['delta_pct']):.0f}% к ср. за 7 дн)\n"
            f"Выручка: {_fmt(s['revenue'])} ₽ · средний чек {_fmt(s['avg_check'])} ₽"
        )

        oos = oos_risk(st.key, t.get("oos_days", 7))
        if oos:
            lines = [
                f"  {o['article'] or o['nm_id']} — {o['qty']} шт, "
                f"{o['days_left']} дн (темп {o['per_day']}/дн)"
                for o in oos[:8]
            ]
            more = f"\n  … ещё {len(oos) - 8}" if len(oos) > 8 else ""
            parts.append("⚠️ Заканчивается остаток:\n" + "\n".join(lines) + more)

        drops = demand_drop(st.key, t.get("drop_pct", 30))
        if drops:
            lines = [
                f"  {d['article'] or d['nm_id']} — {d['yday']} шт "
                f"против {d['avg7']}/дн (−{d['drop']}%)"
                for d in drops[:5]
            ]
            parts.append("📉 Просели заказы:\n" + "\n".join(lines))

        adv = adv_efficiency(st.key, day, t.get("cpo_limit", 300))
        if adv:
            spend = sum(a["spend"] for a in adv)
            aorders = sum(a["orders"] for a in adv)
            head = (f"📣 Реклама: {_fmt(spend)} ₽ / {aorders} заказов"
                    f" · CPO {_fmt(spend / aorders) if aorders else '—'} ₽")
            lines = []
            for a in adv[:5]:
                cpo = f"{a['cpo']:.0f} ₽" if a["cpo"] else "нет заказов"
                warn = " ❗" if a["flag"] else ""
                bud = ""
                if a["budget_days"] is not None and a["budget_days"] < t.get("adv_budget_days", 2):
                    bud = f" · бюджет на {a['budget_days']} дн"
                lines.append(f"  {a['name'][:28]}: {_fmt(a['spend'])} ₽, CPO {cpo}{warn}{bud}")
            parts.append(head + "\n" + "\n".join(lines))

        bad = new_bad_feedbacks(st.key, t.get("bad_rating", 3))
        if bad:
            lines = [
                f"  {b['rating']}★ {b['article'] or b['nm_id']}: {(b['text'] or '')[:70]}"
                for b in bad[:5]
            ]
            parts.append("⭐ Новые низкие оценки:\n" + "\n".join(lines))
            mark_feedbacks_notified(st.key, [b["fb_id"] for b in bad])

        u = unanswered_counts(st.key)
        if u["feedbacks"] or u["questions"]:
            parts.append(f"✉️ Без ответа: отзывов {u['feedbacks']}, вопросов {u['questions']}")

    return "\n".join(parts)
