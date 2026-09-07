"""Расчёты поверх собранных данных."""
from datetime import date, timedelta

from . import db, economics


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


def buyouts_summary(store: str, day: str) -> dict:
    """Выкупы и возвраты за день."""
    row = db.query(
        "SELECT COUNT(*) c, COALESCE(SUM(for_pay),0) s FROM sales "
        "WHERE store=? AND date LIKE ? AND is_return=0",
        (store, f"{day}%"),
    )[0]
    ret = db.query(
        "SELECT COUNT(*) c FROM sales WHERE store=? AND date LIKE ? AND is_return=1",
        (store, f"{day}%"),
    )[0]["c"]
    return {"buyouts": row["c"], "for_pay": row["s"], "returns": ret,
            "redemption": economics.redemption_rate(store) * 100}


def top_returns(store: str, days: int = 30, limit: int = 5) -> list[dict]:
    """Товары, которые возвращают чаще всего."""
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = db.query(
        """
        SELECT nm_id, MIN(article) article,
               SUM(CASE WHEN is_return=1 THEN 1 ELSE 0 END) ret,
               SUM(CASE WHEN is_return=0 THEN 1 ELSE 0 END) sold
        FROM sales WHERE store=? AND date>=?
        GROUP BY nm_id HAVING ret > 0
        """,
        (store, since),
    )
    out = []
    for r in rows:
        total = r["ret"] + r["sold"]
        if total < 5:
            continue
        out.append({
            "nm_id": r["nm_id"], "article": r["article"],
            "returns": r["ret"], "sold": r["sold"],
            "rate": round(r["ret"] / total * 100),
        })
    return sorted(out, key=lambda x: -x["rate"])[:limit]


def promo_risks(store: str, econ: dict, limit: int = 6) -> list[dict]:
    """Акции, где цена по акции опускается ниже точки безубыточности."""
    rows = db.query(
        """
        SELECT pi.promo_id, p.name, p.start_date, pi.nm_id,
               pi.price_now, pi.price_promo, c.cost, c.packaging,
               c.delivery_to_wh, c.other, c.article
        FROM promo_items pi
        JOIN promotions p ON p.store=pi.store AND p.promo_id=pi.promo_id
        LEFT JOIN costs c ON c.store=pi.store AND c.nm_id=pi.nm_id
        WHERE pi.store=?
        """,
        (store,),
    )
    red = economics.redemption_rate(store)
    ad = economics.ad_cost_per_order(store)
    out = []
    for r in rows:
        if r["cost"] is None or not r["price_promo"]:
            continue
        be = economics.break_even(dict(r), econ, red, ad)
        if be.get("min_price") is None:
            continue
        if r["price_promo"] < be["min_price"]:
            out.append({
                "promo": r["name"], "start": (r["start_date"] or "")[:10],
                "nm_id": r["nm_id"], "article": r["article"],
                "price_promo": r["price_promo"],
                "min_price": round(be["min_price"]),
                "gap": round(be["min_price"] - r["price_promo"]),
            })
    return sorted(out, key=lambda x: -x["gap"])[:limit]


def build_digest(stores: list, thresholds: dict, economics_cfg: dict | None = None) -> str:
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

        b = buyouts_summary(st.key, day)
        parts.append(
            f"Выкупы: {b['buyouts']} шт на {_fmt(b['for_pay'])} ₽ к перечислению · "
            f"возвраты {b['returns']} · выкуп {b['redemption']:.0f}% за 30 дн"
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

        econ = (economics_cfg or {}).get(st.key) or (economics_cfg or {}).get("default") or {}
        if econ:
            margins = economics.margin_report(st.key, econ)
            losses = [m for m in margins if m["loss"]]
            if losses:
                lines = [
                    f"  {m['article'] or m['nm_id']}: цена {_fmt(m['price'])} ₽ "
                    f"при минимуме {_fmt(m['min_price'])} ₽ → {_fmt(m['profit'])} ₽/шт"
                    for m in losses[:5]
                ]
                parts.append("🔻 Продаётся в минус:\n" + "\n".join(lines))

            risks = promo_risks(st.key, econ)
            if risks:
                lines = [
                    f"  {r['promo'][:24]} c {r['start']}: {r['article'] or r['nm_id']} "
                    f"по {_fmt(r['price_promo'])} ₽ при минимуме {_fmt(r['min_price'])} ₽"
                    for r in risks[:4]
                ]
                parts.append("🎟 Акции ниже безубыточности:\n" + "\n".join(lines))

        rets = top_returns(st.key)
        if rets:
            lines = [
                f"  {r['article'] or r['nm_id']}: {r['returns']} из {r['returns'] + r['sold']} ({r['rate']}%)"
                for r in rets
            ]
            parts.append("↩️ Чаще всего возвращают (30 дн):\n" + "\n".join(lines))

        u = unanswered_counts(st.key)
        if u["feedbacks"] or u["questions"]:
            parts.append(f"✉️ Без ответа: отзывов {u['feedbacks']}, вопросов {u['questions']}")

    return "\n".join(parts)


AUDIT_CHECKLIST = [
    "Главное фото: понятно за 1–2 секунды, что это и в чём выгода",
    "Остальные слайды: размер, комплектация, материал, детали, сценарий использования",
    "Сравнение с 5–10 конкурентами из первой страницы выдачи",
    "Название: товарный запрос + ключевые характеристики",
    "Характеристики: цвет, размер, состав, назначение, комплектация, страна",
    "Описание отвечает на вопросы покупателя, а не набито ключами",
    "Частые вопросы и причины возвратов разобраны",
    "ТЗ дизайнеру, если нужны новые слайды",
    "Замер через 7–14 дней: клики, заказы, конверсия, выкуп",
]


def audit_candidates(store: str, thresholds: dict) -> list[dict]:
    """SKU, которые стоит разобрать вручную, с причиной попадания в список."""
    reasons: dict[int, dict] = {}

    def add(nm_id, article, reason):
        item = reasons.setdefault(nm_id, {"nm_id": nm_id, "article": article, "why": []})
        item["why"].append(reason)
        if article and not item.get("article"):
            item["article"] = article

    for d in demand_drop(store, thresholds.get("drop_pct", 30)):
        add(d["nm_id"], d["article"], f"заказы упали на {d['drop']}%")

    for r in top_returns(store, limit=10):
        if r["rate"] >= 15:
            add(r["nm_id"], r["article"], f"возвраты {r['rate']}%")

    rows = db.query(
        "SELECT nm_id, MIN(article) article, AVG(rating) avg_r, COUNT(*) n "
        "FROM feedbacks WHERE store=? GROUP BY nm_id HAVING avg_r < 4.5 AND n >= 3",
        (store,),
    )
    for r in rows:
        add(r["nm_id"], r["article"], f"рейтинг {r['avg_r']:.1f} по {r['n']} отзывам")

    rows = db.query(
        "SELECT nm_id, COUNT(*) n FROM questions WHERE store=? AND answered=0 "
        "GROUP BY nm_id HAVING n >= 3",
        (store,),
    )
    for r in rows:
        add(r["nm_id"], None, f"{r['n']} вопросов без ответа — карточка чего-то не объясняет")

    return sorted(reasons.values(), key=lambda x: -len(x["why"]))


def audit_report(stores: list, thresholds: dict) -> str:
    out = []
    for st in stores:
        cands = audit_candidates(st.key, thresholds)
        out.append(f"\n=== {st.name} ===")
        if not cands:
            out.append("Кандидатов на разбор нет.")
            continue
        for c in cands[:10]:
            out.append(f"\n{c['article'] or c['nm_id']} (nmID {c['nm_id']})")
            for w in c["why"]:
                out.append(f"  • {w}")
    out.append("\n--- Чек-лист разбора карточки ---")
    for i, item in enumerate(AUDIT_CHECKLIST, 1):
        out.append(f"{i}. {item}")
    return "\n".join(out)
