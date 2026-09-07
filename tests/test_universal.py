"""Тесты универсальной аналитики без живого WB API."""
from types import SimpleNamespace
from unittest.mock import patch

from wb.universal import format_universal_report, store_snapshot, universal_report


def _store(key: str, name: str):
    return SimpleNamespace(key=key, name=name)


THRESH = {"oos_days": 7, "drop_pct": 30, "cpo_limit": 300, "adv_budget_days": 2}


def _sales(orders, revenue, delta=0.0):
    return {
        "orders": orders,
        "revenue": revenue,
        "avg_check": revenue / orders if orders else 0,
        "avg_7d": 1,
        "delta_pct": delta,
    }


def test_universal_aggregates_two_shops():
    s1, s2 = _store("shop_1", "Текстиль"), _store("shop_2", "Электроника")

    def sales_summary(store, day):
        return _sales(10, 10000, 5) if store == "shop_1" else _sales(5, 8000, -10)

    def buyouts_summary(store, day):
        return {"buyouts": 8, "for_pay": 7000, "returns": 1, "redemption": 80}

    with patch("wb.universal.analytics.yesterday", return_value="2026-09-06"), \
         patch("wb.universal.analytics.sales_summary", side_effect=sales_summary), \
         patch("wb.universal.analytics.buyouts_summary", side_effect=buyouts_summary), \
         patch("wb.universal.analytics.oos_risk", return_value=[]), \
         patch("wb.universal.analytics.demand_drop", return_value=[]), \
         patch("wb.universal.analytics.adv_efficiency", return_value=[]), \
         patch("wb.universal.analytics.unanswered_counts",
               return_value={"feedbacks": 0, "questions": 0}), \
         patch("wb.universal.analytics.top_returns", return_value=[]), \
         patch("wb.universal.analytics.audit_candidates", return_value=[]), \
         patch("wb.universal.analytics.promo_risks", return_value=[]), \
         patch("wb.universal.economics.margin_report", return_value=[]):
        data = universal_report([s1, s2], THRESH, {"default": {}})

    assert data["stores_count"] == 2
    assert data["aggregate"]["orders"] == 15
    assert data["aggregate"]["revenue"] == 18000
    assert data["ranking"][0]["key"] == "shop_1"  # выше выручка
    text = format_universal_report(data)
    assert "Универсальная аналитика" in text
    assert "Текстиль" in text
    assert "Электроника" in text


def test_store_snapshot_severity_counts_oos():
    st = _store("shop_1", "Тест")
    oos = [
        {"nm_id": 1, "article": "a", "qty": 1, "per_day": 1, "days_left": 1},
        {"nm_id": 2, "article": "b", "qty": 10, "per_day": 1, "days_left": 5},
    ]
    with patch("wb.universal.analytics.yesterday", return_value="2026-09-06"), \
         patch("wb.universal.analytics.sales_summary",
               return_value=_sales(1, 100)), \
         patch("wb.universal.analytics.buyouts_summary",
               return_value={"buyouts": 1, "for_pay": 90, "returns": 0, "redemption": 100}), \
         patch("wb.universal.analytics.oos_risk", return_value=oos), \
         patch("wb.universal.analytics.demand_drop", return_value=[]), \
         patch("wb.universal.analytics.adv_efficiency", return_value=[]), \
         patch("wb.universal.analytics.unanswered_counts",
               return_value={"feedbacks": 0, "questions": 0}), \
         patch("wb.universal.analytics.top_returns", return_value=[]), \
         patch("wb.universal.analytics.audit_candidates", return_value=[]), \
         patch("wb.universal.analytics.promo_risks", return_value=[]), \
         patch("wb.universal.economics.margin_report", return_value=[]):
        snap = store_snapshot(st, THRESH, {})

    assert snap["counts"]["oos"] == 2
    assert snap["severity"]["critical"] >= 1
    assert snap["severity"]["warning"] >= 1
