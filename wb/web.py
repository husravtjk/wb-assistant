"""Локальная панель на http://127.0.0.1:8765 — смотреть и запускать руками."""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from . import analytics, db
from .client import Throttle, WBClient
from .collect import collect_store
from .notify import send

PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WB · пульт</title>
<style>
:root{
  --bg:#12161c; --panel:#181e26; --line:#252d38; --ink:#dce3ea; --dim:#7d8b9c;
  --good:#79cfb4; --warn:#e0ad4d; --bad:#e0685f;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:26px;font-weight:600;letter-spacing:-.01em;margin:0 0 4px}
.sub{color:var(--dim);margin:0 0 26px}
.store{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:18px 20px;margin-bottom:16px}
.store h2{font-size:17px;font-weight:600;margin:0 0 14px}
.kpi{display:flex;gap:28px;flex-wrap:wrap;margin-bottom:14px}
.kpi div span{display:block;color:var(--dim);font-size:12px}
.kpi div b{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:8px}
th{text-align:left;color:var(--dim);font-weight:500;padding:5px 8px 5px 0;
  border-bottom:1px solid var(--line)}
td{padding:5px 8px 5px 0;border-bottom:1px solid var(--line);
  font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:0}
.warn{color:var(--warn)} .bad{color:var(--bad)} .good{color:var(--good)}
.sec{color:var(--dim);font-size:12px;margin:16px 0 2px}
button{background:transparent;border:1px solid var(--line);color:var(--ink);
  padding:8px 14px;border-radius:6px;font-size:14px;cursor:pointer;margin-right:8px}
button:hover{border-color:var(--dim)}
button:focus-visible{outline:2px solid var(--good);outline-offset:2px}
.bar{margin-bottom:22px}
#log{color:var(--dim);font-size:13px;margin-top:10px;min-height:20px}
.empty{color:var(--dim);font-size:13.5px}
</style></head><body><div class="wrap">
<h1>Пульт по магазинам</h1>
<p class="sub">Данные за вчера. Обновляется по расписанию, кнопки — для ручного прогона.</p>
<div class="bar">
  <button onclick="run('/api/collect')">Обновить данные</button>
  <button onclick="run('/api/digest/send')">Отправить сводку в Telegram</button>
</div>
<div id="log"></div>
<div id="body">Загружаю…</div>
</div>
<script>
const f = n => (n||0).toLocaleString('ru-RU',{maximumFractionDigits:0});
async function run(url){
  const log = document.getElementById('log');
  log.textContent = 'Выполняю…';
  try{ const r = await fetch(url,{method:'POST'}); const j = await r.json();
       log.textContent = j.message || 'Готово'; load(); }
  catch(e){ log.textContent = 'Не удалось: ' + e.message; }
}
async function load(){
  const r = await fetch('/api/overview'); const data = await r.json();
  document.getElementById('body').innerHTML = data.stores.map(s => {
    const k = s.summary;
    const d = k.delta_pct >= 0 ? 'good' : 'bad';
    let h = `<div class="store"><h2>${s.name}</h2>
      <div class="kpi">
        <div><span>Заказы</span><b>${k.orders}</b></div>
        <div><span>Выручка</span><b>${f(k.revenue)} ₽</b></div>
        <div><span>Средний чек</span><b>${f(k.avg_check)} ₽</b></div>
        <div><span>К среднему за 7 дней</span><b class="${d}">${k.delta_pct>=0?'+':''}${k.delta_pct.toFixed(0)}%</b></div>
      </div>`;
    if(s.oos.length){
      h += `<p class="sec">Заканчивается остаток</p><table>
        <tr><th>Артикул</th><th>Остаток</th><th>Темп/день</th><th>Дней хватит</th></tr>` +
        s.oos.slice(0,10).map(o=>`<tr><td>${o.article||o.nm_id}</td><td>${o.qty}</td>
        <td>${o.per_day}</td><td class="${o.days_left<3?'bad':'warn'}">${o.days_left}</td></tr>`).join('') +
        `</table>`;
    }
    if(s.adv.length){
      h += `<p class="sec">Реклама</p><table>
        <tr><th>Кампания</th><th>Расход</th><th>Заказы</th><th>CPO</th><th>Бюджет</th></tr>` +
        s.adv.slice(0,10).map(a=>`<tr><td>${a.name}</td><td>${f(a.spend)} ₽</td>
        <td>${a.orders}</td><td class="${a.flag?'bad':''}">${a.cpo?f(a.cpo)+' ₽':'—'}</td>
        <td>${a.budget_days!=null?a.budget_days+' дн':'—'}</td></tr>`).join('') + `</table>`;
    }
    if(!s.oos.length && !s.adv.length) h += `<p class="empty">Тревог нет.</p>`;
    return h + `</div>`;
  }).join('');
}
load();
</script></body></html>"""


def create_app(cfg) -> FastAPI:
    app = FastAPI(title="WB Assistant")
    throttle = Throttle(cfg.rate_limits)

    def client(store) -> WBClient:
        return WBClient(store.token, cfg.hosts, throttle, store.key)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE

    @app.get("/api/overview")
    def overview():
        day = analytics.yesterday()
        t = cfg.thresholds
        out = []
        for st in cfg.active_stores:
            out.append({
                "key": st.key, "name": st.name,
                "summary": analytics.sales_summary(st.key, day),
                "oos": analytics.oos_risk(st.key, t.get("oos_days", 7)),
                "adv": analytics.adv_efficiency(st.key, day, t.get("cpo_limit", 300)),
                "drops": analytics.demand_drop(st.key, t.get("drop_pct", 30)),
                "unanswered": analytics.unanswered_counts(st.key),
            })
        return {"date": day, "stores": out}

    @app.post("/api/collect")
    async def collect(store: str | None = None, tasks: str | None = None):
        targets = [s for s in cfg.active_stores if not store or s.key == store]
        task_list = tasks.split(",") if tasks else None
        result = {}
        for st in targets:
            result[st.key] = await collect_store(client(st), st.key, task_list)
        return {"message": "Данные обновлены", "result": result}

    @app.get("/api/digest")
    def digest():
        return {"text": analytics.build_digest(cfg.active_stores, cfg.thresholds)}

    @app.post("/api/digest/send")
    async def digest_send():
        text = analytics.build_digest(cfg.active_stores, cfg.thresholds)
        ok = await send(cfg.telegram.get("bot_token"), cfg.telegram.get("chat_id"), text)
        return {"message": "Сводка отправлена" if ok else "Telegram не настроен"}

    @app.get("/api/runs")
    def runs():
        rows = db.query("SELECT * FROM runs ORDER BY id DESC LIMIT 100")
        return JSONResponse([dict(r) for r in rows])

    @app.get("/api/check")
    async def check():
        out = {}
        for st in cfg.active_stores:
            try:
                await client(st).ping()
                out[st.key] = "ok"
            except Exception as e:  # noqa: BLE001
                out[st.key] = f"ошибка: {e}"
        return out

    return app
