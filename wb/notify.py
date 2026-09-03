import logging

import httpx

log = logging.getLogger("wb.notify")

MAX_LEN = 4000


async def send(bot_token: str, chat_id: str, text: str) -> bool:
    if not bot_token or not chat_id:
        log.warning("Telegram не настроен, сообщение не отправлено")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    chunks = [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)] or [""]
    ok = True
    async with httpx.AsyncClient(timeout=30) as c:
        for chunk in chunks:
            r = await c.post(url, json={
                "chat_id": chat_id, "text": chunk,
                "parse_mode": "HTML", "disable_web_page_preview": True,
            })
            if r.status_code != 200:
                log.error("Telegram %s: %s", r.status_code, r.text[:200])
                ok = False
    return ok
