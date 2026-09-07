"""Недельный отчёт: собирает все блоки анализа и рендерит в HTML.

Дайджест утром отвечает на вопрос «что горит сегодня», отчёт — на вопрос
«куда движется неделя». Поэтому здесь везде сравнение с предыдущими 7 днями.
"""
import html
import os
from datetime import date, datetime, timedelta

from . import analytics, db, economics
from .config import ROOT

REPORTS_DIR = os.path.join(ROOT, "reports")


def _period(weeks_back: int = 0) -> tuple[str, str]:
    end = date.today() - timedelta(days=7 * weeks_back)
    start = end - timedelta(days=7)
    return start.isoformat(), end.isoformat()


def _agg(store: str, start: str, end: str) -> dict:
    o = db.query(
        "SELECT COUNT(*) c, COALESCE(SUM(finished_price),0) s FROM orders "
        "WHERE store=? AND date>=? AND date<? AND is_cancel=0",
        (store, start, end),
    )[0]
    s = db.query(
        "SELECT COUNT(*) c, COALESCE(SUM(for_pay),0) p FROM sales "
        "WHERE store=? AND date>=? AND date<? AND is_return=0",
        (store, start, end),
    )[0]
    r = db.query(
        "SELECT COUNT(*) c FROM sales WHERE store=? AND date>=? AND date<? AND is_return=1",
        (store, start, end),
    )[0]["c"]
    ad = db.query(
        "SELECT COALESCE(SUM(spend),0) s, COALESCE(SUM(orders),0) o FROM adv_stats "
        "WHERE store=? AND date>=? AND date<?",
        (store, start, end),
    )[0]
    return {
        "orders": o["c"], "revenue": o["s"],
        "buyouts": s["c"], "for_pay": s["p"], "returns": r,
        "ad_spend": ad["s"], "ad_orders": ad["o"],
        "redemption": (s["c"] / o["c"] * 100) if o["c"] else 0,
        "drr": (ad["s"] / o["s"] * 100) if o["s"] else 0,
        "cpo": (ad["s"] / ad["o"]) if ad["o"] else 0,
    }


def sku_dynamics(store: str, limit: int = 10) -> list[dict]:
    """Что выросло и что упало по каждому SKU: неделя против предыдущей."""
    s1, e1 = _period(0)
    s2, e2 = _period(1)
    rows = db.query(
        """
        SELECT nm_id, MIN(article) article,
               SUM(CASE WHEN date>=? AND date<? THEN 1 ELSE 0 END) now_c,
               SUM(CASE WHEN date>=? AND date<? THEN 1 ELSE 0 END) prev_c,
               SUM(CASE WHEN date>=? AND date<? THEN finished_price ELSE 0 END) now_s
        FROM orders WHERE store=? AND is_cancel=0 AND date>=?
        GROUP BY nm_id
        """,
        (s1, e1, s2, e2, s1, e1, store, s2),
    )
    out = []
    for r in rows:
        if r["now_c"] == 0 and r["prev_c"] == 0:
            continue
        delta = r["now_c"] - r["prev_c"]
        pct = (delta / r["prev_c"] * 100) if r["prev_c"] else (100 if delta else 0)
        out.append({
            "nm_id": r["nm_id"], "article": r["article"],
            "now": r["now_c"], "prev": r["prev_c"],
            "delta": delta, "pct": round(pct), "revenue": r["now_s"],
        })
    out.sort(key=lambda x: -abs(x["delta"]))
    return out[:limit]


def collect_report(stores: list, thresholds: dict, economics_cfg: dict | None = None) -> dict:
    s1, e1 = _period(0)
    s2, e2 = _period(1)
    blocks = []
    for st in stores:
        econ = (economics_cfg or {}).get(st.key) or (economics_cfg or {}).get("default") or {}
        now, prev = _agg(st.key, s1, e1), _agg(st.key, s2, e2)
        margins = economics.margin_report(st.key, econ) if econ else []
        blocks.append({
            "key": st.key, "name": st.name,
            "now": now, "prev": prev,
            "sku": sku_dynamics(st.key),
            "oos": analytics.oos_risk(st.key, thresholds.get("oos_days", 7)),
            "adv": analytics.adv_efficiency(
                st.key, (date.today() - timedelta(days=1)).isoformat(),
                thresholds.get("cpo_limit", 300)),
            "returns": analytics.top_returns(st.key, limit=8),
            "promo_risks": analytics.promo_risks(st.key, econ) if econ else [],
            "losses": [m for m in margins if m["loss"]],
            "margins": margins[:10],
            "audit": analytics.audit_candidates(st.key, thresholds),
            "unanswered": analytics.unanswered_counts(st.key),
        })
    return {"period": (s1, e1), "prev_period": (s2, e2),
            "generated": datetime.now().isoformat(timespec="minutes"),
            "stores": blocks}


# --- рендер -----------------------------------------------------------------

def _n(v) -> str:
    return f"{v:,.0f}".replace(",", " ")


def _delta(now: float, prev: float) -> str:
    if not prev:
        return '<span class="dim">—</span>'
    pct = (now - prev) / prev * 100
    cls = "up" if pct >= 0 else "down"
    return f'<span class="{cls}">{"+" if pct >= 0 else ""}{pct:.0f}%</span>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return '<p class="empty">Нет данных.</p>'
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_html(data: dict) -> str:
    s1, e1 = data["period"]
    parts = []
    for b in data["stores"]:
        n, p = b["now"], b["prev"]
        kpi = f"""
        <div class="kpi">
          <div><span>Заказы</span><b>{n['orders']}</b>{_delta(n['orders'], p['orders'])}</div>
          <div><span>Выручка</span><b>{_n(n['revenue'])} ₽</b>{_delta(n['revenue'], p['revenue'])}</div>
          <div><span>Выкупы</span><b>{n['buyouts']}</b>{_delta(n['buyouts'], p['buyouts'])}</div>
          <div><span>К перечислению</span><b>{_n(n['for_pay'])} ₽</b>{_delta(n['for_pay'], p['for_pay'])}</div>
          <div><span>Выкуп</span><b>{n['redemption']:.0f}%</b></div>
          <div><span>Возвраты</span><b>{n['returns']}</b>{_delta(n['returns'], p['returns'])}</div>
          <div><span>Реклама</span><b>{_n(n['ad_spend'])} ₽</b>{_delta(n['ad_spend'], p['ad_spend'])}</div>
          <div><span>ДРР</span><b>{n['drr']:.1f}%</b></div>
          <div><span>CPO</span><b>{_n(n['cpo'])} ₽</b></div>
        </div>"""

        sku = _table(
            ["Артикул", "Эта неделя", "Прошлая", "Изменение", "Выручка"],
            [[html.escape(str(s["article"] or s["nm_id"])), str(s["now"]), str(s["prev"]),
              f'<span class="{"up" if s["delta"] >= 0 else "down"}">'
              f'{"+" if s["delta"] >= 0 else ""}{s["delta"]} ({s["pct"]}%)</span>',
              f'{_n(s["revenue"])} ₽'] for s in b["sku"]])

        oos = _table(
            ["Артикул", "Остаток", "Темп/день", "Дней хватит"],
            [[html.escape(str(o["article"] or o["nm_id"])), str(o["qty"]), str(o["per_day"]),
              f'<span class="{"down" if o["days_left"] < 3 else "warn"}">{o["days_left"]}</span>']
             for o in b["oos"][:12]])

        adv = _table(
            ["Кампания", "Расход", "Заказы", "CPO", "Бюджет"],
            [[html.escape(str(a["name"])), f'{_n(a["spend"])} ₽', str(a["orders"]),
              (f'<span class="down">{_n(a["cpo"])} ₽</span>' if a["flag"] and a["cpo"]
               else (f'{_n(a["cpo"])} ₽' if a["cpo"] else '<span class="down">нет заказов</span>')),
              f'{a["budget_days"]} дн' if a["budget_days"] is not None else "—"]
             for a in b["adv"][:12]])

        losses = _table(
            ["Артикул", "Цена", "Минимум", "Прибыль/шт"],
            [[html.escape(str(m["article"] or m["nm_id"])), f'{_n(m["price"])} ₽',
              f'{_n(m["min_price"])} ₽',
              f'<span class="down">{_n(m["profit"])} ₽</span>'] for m in b["losses"][:12]])

        promo = _table(
            ["Акция", "Старт", "Артикул", "Цена по акции", "Минимум"],
            [[html.escape(str(r["promo"])), r["start"],
              html.escape(str(r["article"] or r["nm_id"])),
              f'<span class="down">{_n(r["price_promo"])} ₽</span>', f'{_n(r["min_price"])} ₽']
             for r in b["promo_risks"]])

        rets = _table(
            ["Артикул", "Возвраты", "Продажи", "Доля"],
            [[html.escape(str(r["article"] or r["nm_id"])), str(r["returns"]), str(r["sold"]),
              f'<span class="{"down" if r["rate"] >= 15 else ""}">{r["rate"]}%</span>']
             for r in b["returns"]])

        audit = "".join(
            f'<li><b>{html.escape(str(c["article"] or c["nm_id"]))}</b> '
            f'<span class="dim">nmID {c["nm_id"]}</span><br>'
            + " · ".join(html.escape(w) for w in c["why"]) + "</li>"
            for c in b["audit"][:10]) or '<li class="empty">Кандидатов нет.</li>'

        parts.append(f"""
        <section>
          <h2>{html.escape(b['name'])}</h2>
          {kpi}
          <h3>Динамика по SKU</h3>{sku}
          <h3>Остатки под риском</h3>{oos}
          <h3>Реклама</h3>{adv}
          <h3>Продаётся в минус</h3>{losses}
          <h3>Акции ниже безубыточности</h3>{promo}
          <h3>Возвраты</h3>{rets}
          <h3>Карточки на разбор</h3><ul class="audit">{audit}</ul>
          <p class="dim">Без ответа: отзывов {b['unanswered']['feedbacks']},
             вопросов {b['unanswered']['questions']}</p>
        </section>""")

    checklist = "".join(f"<li>{html.escape(i)}</li>" for i in analytics.AUDIT_CHECKLIST)

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Недельный отчёт {s1} — {e1}</title>
<style>
:root{{--bg:#12161c;--panel:#181e26;--line:#252d38;--ink:#dce3ea;--dim:#7d8b9c;
--up:#79cfb4;--warn:#e0ad4d;--down:#e0685f}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1100px;margin:0 auto;padding:32px 20px 80px}}
h1{{font-size:28px;font-weight:600;letter-spacing:-.01em;margin:0 0 4px}}
h2{{font-size:20px;font-weight:600;margin:0 0 16px}}
h3{{font-size:13px;font-weight:500;color:var(--dim);text-transform:uppercase;
letter-spacing:.05em;margin:24px 0 6px}}
section{{background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:22px 24px;margin-bottom:18px}}
.kpi{{display:flex;gap:26px;flex-wrap:wrap}}
.kpi div{{min-width:110px}}
.kpi span{{display:block;color:var(--dim);font-size:12px}}
.kpi b{{font-size:21px;font-weight:600;font-variant-numeric:tabular-nums;margin-right:6px}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}}
th{{text-align:left;color:var(--dim);font-weight:500;padding:6px 10px 6px 0;
border-bottom:1px solid var(--line)}}
td{{padding:6px 10px 6px 0;border-bottom:1px solid var(--line);
font-variant-numeric:tabular-nums}}
tr:last-child td{{border-bottom:0}}
.up{{color:var(--up)}} .down{{color:var(--down)}} .warn{{color:var(--warn)}}
.dim,.empty{{color:var(--dim);font-size:13px}}
ul.audit{{list-style:none;padding:0;margin:0}}
ul.audit li{{padding:8px 0;border-bottom:1px solid var(--line);font-size:13.5px}}
ul.audit li:last-child{{border-bottom:0}}
ol{{color:var(--dim);font-size:13.5px;padding-left:20px}}
.head{{margin-bottom:26px}}
@media print{{body{{background:#fff;color:#000}}section{{border-color:#ccc}}}}
</style></head><body><div class="wrap">
<div class="head">
  <h1>Недельный отчёт</h1>
  <p class="dim">{s1} — {e1} · сравнение с {data['prev_period'][0]} — {data['prev_period'][1]}
     · сформирован {data['generated']}</p>
</div>
{''.join(parts)}
<section>
  <h2>Чек-лист разбора карточки</h2>
  <ol>{checklist}</ol>
</section>
</div></body></html>"""


def save(data: dict) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, f"{data['period'][1]}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html(data))
    return path


def telegram_summary(data: dict) -> str:
    s1, e1 = data["period"]
    lines = [f"<b>Недельный отчёт {s1} — {e1}</b>"]
    for b in data["stores"]:
        n, p = b["now"], b["prev"]
        d = ((n["orders"] - p["orders"]) / p["orders"] * 100) if p["orders"] else 0
        lines.append(
            f"\n<b>{b['name']}</b>\n"
            f"Заказы {n['orders']} ({'+' if d >= 0 else ''}{d:.0f}%) · "
            f"выручка {_n(n['revenue'])} ₽ · выкуп {n['redemption']:.0f}%\n"
            f"Реклама {_n(n['ad_spend'])} ₽ · ДРР {n['drr']:.1f}% · CPO {_n(n['cpo'])} ₽"
        )
        flags = []
        if b["oos"]:
            flags.append(f"остатки на исходе: {len(b['oos'])} SKU")
        if b["losses"]:
            flags.append(f"в минусе: {len(b['losses'])} SKU")
        if b["promo_risks"]:
            flags.append(f"рискованных акций: {len(b['promo_risks'])}")
        if b["audit"]:
            flags.append(f"на разбор: {len(b['audit'])} карточек")
        if flags:
            lines.append("⚠️ " + " · ".join(flags))
    lines.append("\nПолный отчёт — на панели: /report")
    return "\n".join(lines)
