"""Универсальная аналитика по всем магазинам.

Одинаковый набор KPI на каждый кабинет + агрегат сверху.
Считается поверх уже собранных данных в SQLite.
"""
from __future__ import annotations

from typing import Any

from . import analytics, economics


def _econ_for(store_key: str, economics_cfg: dict | None) -> dict:
    cfg = economics_cfg or {}
    return cfg.get(store_key) or cfg.get("default") or {}


def store_snapshot(
    store: Any,
    thresholds: dict,
    economics_cfg: dict | None = None,
) -> dict:
    """Полный срез одного магазина в едином формате."""
    day = analytics.yesterday()
    t = thresholds or {}
    key = store.key
    econ = _econ_for(key, economics_cfg)

    sales = analytics.sales_summary(key, day)
    buyouts = analytics.buyouts_summary(key, day)
    oos = analytics.oos_risk(key, t.get("oos_days", 7))
    drops = analytics.demand_drop(key, t.get("drop_pct", 30))
    adv = analytics.adv_efficiency(key, day, t.get("cpo_limit", 300))
    unanswered = analytics.unanswered_counts(key)
    returns_top = analytics.top_returns(key, limit=8)
    audit = analytics.audit_candidates(key, t)

    losses: list[dict] = []
    promo: list[dict] = []
    if econ:
        margins = economics.margin_report(key, econ)
        losses = [m for m in margins if m.get("loss")]
        promo = analytics.promo_risks(key, econ)

    adv_spend = sum(a["spend"] for a in adv)
    adv_orders = sum(a["orders"] for a in adv)
    adv_flagged = [a for a in adv if a.get("flag")]
    budget_warn = [
        a for a in adv
        if a.get("budget_days") is not None
        and a["budget_days"] < t.get("adv_budget_days", 2)
    ]

    critical = 0
    warning = 0
    # OOS now (qty 0 with demand) already filtered in oos_risk by days_left
    critical += sum(1 for o in oos if o["days_left"] < 3 or o["qty"] == 0)
    warning += sum(1 for o in oos if o["days_left"] >= 3)
    warning += len(drops)
    critical += len(losses)
    critical += len(promo)
    critical += sum(1 for a in adv_flagged if a.get("orders", 0) == 0 and a.get("spend", 0) > 0)
    warning += sum(1 for a in adv_flagged if a.get("orders", 0) > 0)
    warning += len(budget_warn)
    warning += len(audit)
    if unanswered["feedbacks"] or unanswered["questions"]:
        warning += 1

    return {
        "key": key,
        "name": store.name,
        "date": day,
        "sales": sales,
        "buyouts": buyouts,
        "oos": oos,
        "drops": drops,
        "adv": adv,
        "adv_totals": {
            "spend": adv_spend,
            "orders": adv_orders,
            "cpo": (adv_spend / adv_orders) if adv_orders else None,
            "flagged": len(adv_flagged),
            "budget_warn": len(budget_warn),
        },
        "unanswered": unanswered,
        "returns_top": returns_top,
        "losses": losses,
        "promo_risks": promo,
        "audit": audit,
        "severity": {"critical": critical, "warning": warning},
        "counts": {
            "oos": len(oos),
            "drops": len(drops),
            "losses": len(losses),
            "promo_risks": len(promo),
            "audit": len(audit),
            "adv_flagged": len(adv_flagged),
        },
    }


def universal_report(
    stores: list,
    thresholds: dict,
    economics_cfg: dict | None = None,
) -> dict:
    """Срез по всем магазинам + агрегат."""
    snapshots = [store_snapshot(st, thresholds, economics_cfg) for st in stores]
    day = analytics.yesterday()

    agg = {
        "orders": 0,
        "revenue": 0.0,
        "buyouts": 0,
        "for_pay": 0.0,
        "returns": 0,
        "ad_spend": 0.0,
        "ad_orders": 0,
        "oos": 0,
        "drops": 0,
        "losses": 0,
        "promo_risks": 0,
        "audit": 0,
        "critical": 0,
        "warning": 0,
        "unanswered_feedbacks": 0,
        "unanswered_questions": 0,
    }
    for s in snapshots:
        agg["orders"] += s["sales"]["orders"]
        agg["revenue"] += s["sales"]["revenue"]
        agg["buyouts"] += s["buyouts"]["buyouts"]
        agg["for_pay"] += s["buyouts"]["for_pay"]
        agg["returns"] += s["buyouts"]["returns"]
        agg["ad_spend"] += s["adv_totals"]["spend"]
        agg["ad_orders"] += s["adv_totals"]["orders"]
        agg["oos"] += s["counts"]["oos"]
        agg["drops"] += s["counts"]["drops"]
        agg["losses"] += s["counts"]["losses"]
        agg["promo_risks"] += s["counts"]["promo_risks"]
        agg["audit"] += s["counts"]["audit"]
        agg["critical"] += s["severity"]["critical"]
        agg["warning"] += s["severity"]["warning"]
        agg["unanswered_feedbacks"] += s["unanswered"]["feedbacks"]
        agg["unanswered_questions"] += s["unanswered"]["questions"]

    agg["avg_check"] = (agg["revenue"] / agg["orders"]) if agg["orders"] else 0
    agg["cpo"] = (agg["ad_spend"] / agg["ad_orders"]) if agg["ad_orders"] else None
    agg["redemption_pct"] = (
        agg["buyouts"] / (agg["buyouts"] + agg["returns"]) * 100
        if (agg["buyouts"] + agg["returns"]) else None
    )

    ranking = sorted(
        snapshots,
        key=lambda s: (s["sales"]["revenue"], s["sales"]["orders"]),
        reverse=True,
    )
    ranking_brief = [
        {
            "key": s["key"],
            "name": s["name"],
            "orders": s["sales"]["orders"],
            "revenue": s["sales"]["revenue"],
            "critical": s["severity"]["critical"],
            "warning": s["severity"]["warning"],
        }
        for s in ranking
    ]

    return {
        "date": day,
        "stores_count": len(snapshots),
        "aggregate": agg,
        "ranking": ranking_brief,
        "stores": snapshots,
    }


def _fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def format_universal_report(data: dict) -> str:
    """Текстовый отчёт для CLI / Telegram-стиля."""
    day = data["date"]
    a = data["aggregate"]
    parts = [
        f"Универсальная аналитика за {day}",
        f"Магазинов: {data['stores_count']}",
        "",
        "ИТОГО ПО ВСЕМ",
        f"Заказы: {a['orders']} · выручка {_fmt(a['revenue'])} ₽ · чек {_fmt(a['avg_check'])} ₽",
        f"Выкупы: {a['buyouts']} · возвраты {a['returns']}"
        + (f" · выкуп {a['redemption_pct']:.0f}%" if a["redemption_pct"] is not None else ""),
        f"Реклама: {_fmt(a['ad_spend'])} ₽ / {a['ad_orders']} зак."
        + (f" · CPO {_fmt(a['cpo'])} ₽" if a["cpo"] is not None else " · CPO —"),
        f"Риски: 🔴 {a['critical']} критичных · 🟡 {a['warning']} внимания",
        f"OOS {a['oos']} · просадки {a['drops']} · в минусе {a['losses']} · "
        f"акции {a['promo_risks']} · аудит {a['audit']}",
        f"Без ответа: отзывов {a['unanswered_feedbacks']}, "
        f"вопросов {a['unanswered_questions']}",
    ]

    if data["ranking"]:
        parts.append("")
        parts.append("Рейтинг магазинов (по выручке вчера)")
        for i, r in enumerate(data["ranking"], 1):
            parts.append(
                f"{i}. {r['name']}: {_fmt(r['revenue'])} ₽ · "
                f"{r['orders']} зак. · 🔴{r['critical']} 🟡{r['warning']}"
            )

    for s in data["stores"]:
        sal = s["sales"]
        arrow = "▲" if sal["delta_pct"] >= 0 else "▼"
        parts.append("")
        parts.append(f"=== {s['name']} ===")
        parts.append(
            f"Заказы {sal['orders']} ({arrow}{abs(sal['delta_pct']):.0f}% к 7д) · "
            f"выручка {_fmt(sal['revenue'])} ₽"
        )
        b = s["buyouts"]
        parts.append(
            f"Выкупы {b['buyouts']} · возвраты {b['returns']} · "
            f"выкуп {b['redemption']:.0f}% за 30д"
        )
        at = s["adv_totals"]
        cpo = f"{_fmt(at['cpo'])} ₽" if at["cpo"] is not None else "—"
        parts.append(
            f"Реклама {_fmt(at['spend'])} ₽ / {at['orders']} зак. · CPO {cpo} · "
            f"флагов {at['flagged']}"
        )
        c = s["counts"]
        sev = s["severity"]
        parts.append(
            f"Риски 🔴{sev['critical']} 🟡{sev['warning']} · "
            f"OOS {c['oos']} · просадки {c['drops']} · "
            f"минус {c['losses']} · аудит {c['audit']}"
        )
        if s["oos"][:3]:
            lines = [
                f"  {o['article'] or o['nm_id']}: {o['qty']} шт, {o['days_left']} дн"
                for o in s["oos"][:3]
            ]
            parts.append("OOS топ:\n" + "\n".join(lines))
        if s["drops"][:3]:
            lines = [
                f"  {d['article'] or d['nm_id']}: {d['yday']} vs {d['avg7']}/дн (−{d['drop']}%)"
                for d in s["drops"][:3]
            ]
            parts.append("Просадки топ:\n" + "\n".join(lines))

    return "\n".join(parts)
