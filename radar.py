import os
import re
import json
import time
import requests

GEMINI_KEY = os.environ.get("GEMINI_KEY", "").strip()
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").strip()
LIFETIME_MS = 40 * 60 * 1000  # 40 минут

CHANNELS = [
    {
        "id": "dnepr_bez_tck",
        "url": "https://t.me/s/dnepr_bez_tck",
        "name": "Канал 1",
        "color_clean": "green",
        "color_danger": "red"
    },
    {
        "id": "agendaDnepr",
        "url": "https://t.me/s/agendaDnepr",
        "name": "Канал 2",
        "color_clean": "blue",
        "color_danger": "purple"
    }
]

if FIREBASE_URL and not FIREBASE_URL.endswith(".json"):
    FIREBASE_URL = FIREBASE_URL.rstrip("/") + "/points.json"

def log(msg):
    print(msg, flush=True)

def find_working_model():
    for api_ver in ["v1beta", "v1"]:
        for model_name in ["gemini-flash-lite-latest", "gemini-1.5-flash-latest", "gemini-2.0-flash"]:
            test_url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model_name}:generateContent?key={GEMINI_KEY}"
            try:
                r = requests.post(test_url, json={"contents": [{"parts": [{"text": "ping"}]}]}, timeout=6)
                if r.status_code == 200:
                    log(f"🎯 Активная модель: {model_name} ({api_ver})")
                    return api_ver, model_name
            except Exception:
                continue
    return "v1beta", "gemini-flash-lite-latest"

API_VERSION, MODEL_NAME = find_working_model()

def cleanup_old_points():
    try:
        r = requests.get(FIREBASE_URL, timeout=8)
        if r.status_code == 200 and r.json():
            data = r.json()
            now_ms = int(time.time() * 1000)
            to_delete = {k: None for k, v in data.items() if isinstance(v, dict) and (now_ms - (v.get("time") or v.get("timestamp") or 0)) > LIFETIME_MS}
            if to_delete:
                base_url = FIREBASE_URL.replace("/points.json", "")
                requests.patch(f"{base_url}/points.json", json=to_delete, timeout=5)
                log(f"🧹 Удалено устаревших точек: {len(to_delete)}")
    except Exception as e:
        log(f"Ошибка очистки базы: {e}")

def get_existing_records():
    try:
        r = requests.get(FIREBASE_URL, timeout=8)
        if r.status_code == 200 and r.json():
            data = r.json()
            records = set()
            for v in data.values():
                if isinstance(v, dict):
                    src = v.get("source", "")
                    raw = v.get("raw_text") or v.get("text", "")
                    raw = raw.replace(" (чисто)", "").strip()
                    records.add((src, raw))
            return records
    except Exception as e:
        log(f"Ошибка базы: {e}")
    return set()

def fetch_tg_posts(channel_url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(channel_url, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        raw_posts = re.findall(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', r.text, re.DOTALL)
        clean = []
        for p in raw_posts:
            t = re.sub(r'<[^>]+>', '', p).strip()
            if t:
                clean.append(t)
        return clean[-10:]
    except Exception as e:
        log(f"Ошибка загрузки {channel_url}: {e}")
        return []

def parse_batch_gemini(posts_list):
    if not posts_list:
        return []

    cleaned_posts = [p.replace('"', "'").replace('\\', '/').replace('\n', ' ').strip() for p in posts_list]
    items = "\n".join([f"[{i}] {p}" for i, p in enumerate(cleaned_posts)])
    url = f"https://generativelanguage.googleapis.com/{API_VERSION}/models/{MODEL_NAME}:generateContent?key={GEMINI_KEY}"
    
    prompt = f"""Ты опытный диспетчер дорожной обстановки в городе Днепр (Украина).
Определи локацию в г. Днепр и статус опасности.

СТРОГИЕ ТОЧНЫЕ КООРДИНАТЫ ДЛЯ НАРОДНЫХ ОРИЕНТИРОВ:
1. "Краснозаводская" / "Белелюбского" -> ул. Академика Белелюбского (бывш. Краснозаводская) возле ДТРЗ [lat: 48.4812, lng: 34.9940]
2. "Павлова" / "угол Павлова" -> ул. Академика Павлова угол Белелюбского [lat: 48.4818, lng: 35.0012]
3. "Комбайновый" -> ул. Белелюбского за 2-м поворотом от Павлова [lat: 48.4795, lng: 34.9845]
4. "Водолечебница" -> пр. Свободы, 2 перед Кайдакским мостом [lat: 48.4848, lng: 34.9735]
5. "Кайдакский съезд" / "РОВД" / "съезд с кайдакского" -> съезд с Кайдакского моста на ул. Кайдакский Шлях [lat: 48.4925, lng: 34.9625]
6. "Речпорт" / "речной порт" -> Речной вокзал / Набережная Заводская / пл. Десантников [lat: 48.4805, lng: 35.0210]
7. "Водокачка" / "водокачка набережная" -> Набережная Заводская район водокачки [lat: 48.4910, lng: 34.9480]
8. "Стан" / "Стан 550" / "Набережная Заводская" -> Набережная Заводская, район Стана 550 [lat: 48.4865, lng: 34.9850]
   (Внимание: общие слова "набережная" или "набка" БЕЗ слова Заводская или Стан сюда НЕ относить!).
9. "Огни" -> Вечный огонь на пр. Сергея Нигояна угол с пр. Ивана Мазепы [lat: 48.4716, lng: 34.9892]

ПРАВИЛО БП:
Слова "БП", "б.п.", "б/п", "блокпост", "бус", "патруль", "проверка", "дождь", "готовят" — это признак опасности (status: danger), адрес бери из самого названия места!

СТАТУС:
- danger: бп, б.п., б/п, блокпост, мобпост, бус, поливают, дождь, оливки, синие, пишут, обилечивают, тормозят, готовят, 🫒, 🌧️, ⚡.
- clean: чисто, сухо, пусто, спокойно, ок, 👍, 🫡, ✌️, ☀️, 🟢.

Сообщения:
{items}

Верни строго JSON массив:
[
  {{"id": 0, "valid": true, "address": "Название места", "lat": 48.4865, "lng": 34.9850, "status": "clean"}},
  {{"id": 1, "valid": false}}
]"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }

    try:
        resp = requests.post(url, json=payload, timeout=20)
        if resp.status_code == 200:
            raw_text = resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            parsed = json.loads(raw_text)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, list):
                        return v
                return [parsed]
    except Exception as e:
        log(f"Ошибка вызова Gemini: {e}")
    return []

def is_danger_text(text):
    t = text.lower()
    danger_patterns = [
        r'\bбп\b', r'б\.п', r'б/п', r'блокпост', r'блок\s*пост', r'мобпост',
        r'фишк', r'шлагбаум', r'бус', r'патрул', r'дожд', r'туч', r'хмар',
        r'злив', r'оливк', r'баклажан', r'синие', r'зелен', r'пиш[уе]', r'разда',
        r'обилеч', r'тормоз', r'останавл', r'провер', r'паку', r'готовят'
    ]
    return any(re.search(p, t) for p in danger_patterns)

def sync_cycle():
    cleanup_old_points()
    existing_records = get_existing_records()

    for ch in CHANNELS:
        posts = fetch_tg_posts(ch["url"])
        new_posts = [p for p in posts if (ch["id"], p) not in existing_records]
        log(f"📡 [{ch['name']}] Всего: {len(posts)} | Новых: {len(new_posts)}")

        if not new_posts:
            continue

        results = parse_batch_gemini(new_posts)
        added_count = 0

        for item in results:
            idx = item.get("id")
            if idx is not None and 0 <= idx < len(new_posts):
                post = new_posts[idx]
                if item.get("valid") and "lat" in item and "lng" in item:
                    now_ms = int(time.time() * 1000)
                    force_danger = is_danger_text(post)
                    has_clean = any(s in post for s in ["👍", "🫡", "✌️", "👌", "☀️", "🟢", "чисто", "спокійно", "ясно", "пусто", "сухо"])
                    
                    if force_danger:
                        is_clean = False
                    elif item.get("status", "").lower() == "danger":
                        is_clean = False
                    elif item.get("status", "").lower() == "clean" or has_clean:
                        is_clean = True
                    else:
                        is_clean = False

                    marker_color = ch["color_clean"] if is_clean else ch["color_danger"]
                    clean_tag = " (чисто)" if (is_clean and "чисто" not in post.lower()) else ""
                    final_text = f"{post}{clean_tag}"

                    payload = {
                        "text": final_text,
                        "raw_text": post,
                        "address": item.get("address", "Дніпро"),
                        "lat": float(item["lat"]),
                        "lng": float(item["lng"]),
                        "status": "чисто" if is_clean else "опасно",
                        "color": marker_color,
                        "time": now_ms,
                        "timestamp": now_ms,
                        "source": ch["id"],
                        "channel_name": ch["name"]
                    }
                    try:
                        r = requests.post(FIREBASE_URL, json=payload, timeout=8)
                        if r.status_code == 200:
                            log(f"  ✅ [{ch['name']}] Нанесено ({marker_color}): {item.get('address')}")
                            existing_records.add((ch["id"], post))
                            added_count += 1
                    except Exception as e:
                        log(f"Ошибка сохранения: {e}")

        log(f"  🏁 [{ch['name']}] Обработано! Добавлено: {added_count}")

if __name__ == "__main__":
    log("🚀 Запуск быстрого радара...")
    for step in range(8):
        sync_cycle()
        if step < 7:
            time.sleep(60)
    log("🏁 Цикл завершён!")
