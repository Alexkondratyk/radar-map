import os
import re
import json
import time
import requests

GEMINI_KEY = os.environ.get("GEMINI_KEY")
FIREBASE_URL = os.environ.get("FIREBASE_URL")
TG_URL = "https://t.me/s/dnepr_bez_tck"

def get_existing_messages():
    """Получает тексты сообщений, которые уже сохранены в базе, чтобы не дублировать их."""
    try:
        r = requests.get(FIREBASE_URL, timeout=10)
        if r.status_code == 200 and r.json():
            data = r.json()
            return {v.get("text", "").strip() for v in data.values() if isinstance(v, dict)}
    except Exception as e:
        print(f"Ошибка проверки базы: {e}")
    return set()

def fetch_tg_posts():
    """Забирает последние сообщения из открытого веб-зеркала Telegram."""
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(TG_URL, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        raw_posts = re.findall(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', r.text, re.DOTALL)
        clean = []
        for p in raw_posts:
            text = re.sub(r'<[^>]+>', '', p).strip()
            if text:
                clean.append(text)
        return clean[-10:]
    except Exception as e:
        print(f"Ошибка загрузки постов: {e}")
        return []

def parse_with_gemini(text):
    """Отправляет пост в Gemini для извлечения координат Днепра."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    prompt = f"""Ты картографический аналитик города Днепр (Украина).
Проанализируй текст сообщения: "{text}"
Определи точную локацию/перекресток/район в черте г. Днепр.
Верни строго чистый JSON без markdown (без ```json):
{{"valid": true, "address": "краткое место", "lat": 48.46..., "lng": 35.04...}}
Если локации в Днепре нет, спам, опрос или реклама — верни строго:
{{"valid": false}}"""

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    # До 3 попыток на случай перегрузки 503
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                res_json = resp.json()
                raw_text = res_json['candidates'][0]['content']['parts'][0]['text']
                clean_json = re.sub(r'```json|```', '', raw_text).strip()
                return json.loads(clean_json)
            elif resp.status_code in [429, 503]:
                time.sleep(5)
        except Exception:
            time.sleep(3)
    return None

def sync_cycle():
    existing = get_existing_messages()
    posts = fetch_tg_posts()

    for post in posts:
        if post in existing:
            continue

        print(f"Новый пост: {post[:40]}...")
        result = parse_with_gemini(post)

        if result and result.get("valid") and "lat" in result and "lng" in result:
            payload = {
                "text": post,
                "address": result.get("address", "Днепр"),
                "lat": float(result["lat"]),
                "lng": float(result["lng"]),
                "time": int(time.time()),
                "source": "dnepr_bez_tck"
            }
            try:
                r = requests.post(FIREBASE_URL, json=payload, timeout=10)
                if r.status_code == 200:
                    print(f"-> Точка сохранена: {result.get('address')}")
                    existing.add(post)
            except Exception as e:
                print(f"Ошибка сохранения: {e}")

        time.sleep(3)  # Пауза между запросами к Gemini

if __name__ == "__main__":
    print("Старт цикла проверки (10 минут с шагом 60 секунд)...")
    # Цикл крутится 10 минут, опрашивая канал каждую минуту
    for step in range(10):
        print(f"--- Проверка {step + 1}/10 ---")
        sync_cycle()
        if step < 9:
            time.sleep(60)
