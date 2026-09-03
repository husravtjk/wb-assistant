import sqlite3
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS orders (
    store TEXT, srid TEXT, date TEXT, last_change TEXT,
    nm_id INTEGER, article TEXT, brand TEXT, subject TEXT, category TEXT,
    total_price REAL, discount_pct REAL, finished_price REAL,
    warehouse TEXT, region TEXT, is_cancel INTEGER,
    PRIMARY KEY (store, srid)
);
CREATE INDEX IF NOT EXISTS ix_orders_date ON orders(store, date);
CREATE INDEX IF NOT EXISTS ix_orders_nm ON orders(store, nm_id, date);

CREATE TABLE IF NOT EXISTS sales (
    store TEXT, sale_id TEXT, date TEXT, nm_id INTEGER, article TEXT,
    finished_price REAL, for_pay REAL, price_with_disc REAL,
    warehouse TEXT, region TEXT,
    PRIMARY KEY (store, sale_id)
);
CREATE INDEX IF NOT EXISTS ix_sales_date ON sales(store, date);

CREATE TABLE IF NOT EXISTS stocks (
    store TEXT, snap_date TEXT, nm_id INTEGER, article TEXT, barcode TEXT,
    warehouse TEXT, subject TEXT, brand TEXT,
    quantity INTEGER, in_way_to_client INTEGER, in_way_from_client INTEGER,
    quantity_full INTEGER, price REAL, discount REAL,
    PRIMARY KEY (store, snap_date, nm_id, barcode, warehouse)
);
CREATE INDEX IF NOT EXISTS ix_stocks_snap ON stocks(store, snap_date);

CREATE TABLE IF NOT EXISTS adv_campaigns (
    store TEXT, advert_id INTEGER, name TEXT, type INTEGER, status INTEGER,
    daily_budget REAL, budget REAL, updated TEXT,
    PRIMARY KEY (store, advert_id)
);

CREATE TABLE IF NOT EXISTS adv_stats (
    store TEXT, date TEXT, advert_id INTEGER,
    views INTEGER, clicks INTEGER, ctr REAL, cpc REAL,
    spend REAL, orders INTEGER, sum_price REAL,
    PRIMARY KEY (store, date, advert_id)
);

CREATE TABLE IF NOT EXISTS feedbacks (
    store TEXT, fb_id TEXT, date TEXT, nm_id INTEGER, article TEXT,
    rating INTEGER, text TEXT, answered INTEGER, notified INTEGER DEFAULT 0,
    PRIMARY KEY (store, fb_id)
);

CREATE TABLE IF NOT EXISTS questions (
    store TEXT, q_id TEXT, date TEXT, nm_id INTEGER,
    text TEXT, answered INTEGER, notified INTEGER DEFAULT 0,
    PRIMARY KEY (store, q_id)
);

CREATE TABLE IF NOT EXISTS prices (
    store TEXT, nm_id INTEGER, article TEXT,
    price REAL, discount REAL, club_discount REAL, updated TEXT,
    PRIMARY KEY (store, nm_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store TEXT, kind TEXT, key TEXT, message TEXT, created TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_key ON alerts(store, kind, key, created);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store TEXT, task TEXT, status TEXT, detail TEXT, created TEXT
);
"""


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert(table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ",".join("?" * len(cols))
    sql = (
        f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    )
    data = [tuple(r.get(c) for c in cols) for r in rows]
    with connect() as c:
        c.executemany(sql, data)
    return len(data)


def query(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with connect() as c:
        return c.execute(sql, args).fetchall()


def log_run(store: str, task: str, status: str, detail: str = "") -> None:
    from datetime import datetime
    upsert("runs", [{
        "store": store, "task": task, "status": status,
        "detail": detail[:500], "created": datetime.now().isoformat(timespec="seconds"),
    }])


def alert_sent_recently(store: str, kind: str, key: str, hours: int = 20) -> bool:
    rows = query(
        "SELECT 1 FROM alerts WHERE store=? AND kind=? AND key=? "
        "AND created > datetime('now', ?) LIMIT 1",
        (store, kind, key, f"-{hours} hours"),
    )
    return bool(rows)


def record_alert(store: str, kind: str, key: str, message: str) -> None:
    from datetime import datetime
    upsert("alerts", [{
        "store": store, "kind": kind, "key": key, "message": message[:1000],
        "created": datetime.now().isoformat(timespec="seconds"),
    }])
