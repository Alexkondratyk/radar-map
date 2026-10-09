import os
import re
import json
import time
import requests

GEMINI_KEY = os.environ.get("GEMINI_KEY", "").strip()
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").strip()
LIFETIME_MS = 40 * 60 * 1000  # 40 минут для живой карты
WEEK_MS = 7 * 24 * 60 * 60 * 1000  # 7 дней для архива статистики

CHANNEL_URL = "https://t.me/s/dnepr_bez_tck"

if FIREBASE_URL and not FIREBASE_URL.endswith(".json"):
    FIREBASE_URL = FIREBASE_URL.rstrip("/") + "/points.json"

KNOWLEDGE_BASE_URL = FIREBASE_URL.replace("/points.json", "/knowledge_base.json")
STATS_ARCHIVE_URL = FIREBASE_URL.replace("/points.json", "/stats_archive.json")

def log(msg):
    print(msg, flush=True)

def find_working_model():
    for api_ver in ["v1beta", "v1"]:
        for model_name in ["gemini-flash-lite-latest", "gemini-1.5-flash-latest", "gemini-2.0-flash"]:
            test_url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model_name}:generateContent?key={GEMINI_KEY}"
            try:
                r = requests.post(test_url, json={"contents": [{"parts": [{"text": "ping"}]}]}, timeout=6)
                if r.status_code == 200:
                    log(f"🎯 Модель: {model_name} ({api_ver})")
                    return api_ver, model_name
            except Exception:
                continue
    return "v1beta", "gemini-flash-lite-latest"

API_VERSION, MODEL_NAME = find_working_model()

def get_knowledge_base():
    try:
        r = requests.get(f"{KNOWLEDGE_BASE_URL}?t={int(time.time())}", timeout=8)
        if r.status_code == 200 and r.json():
            data = r.json()
            if isinstance(data, dict):
                return list(data.values())
            elif isinstance(data, list):
                return [x for x in data if x]
    except Exception as e:
        log(f"Ошибка загрузки базы знаний: {e}")
    return []

def cleanup_old_points():
    now_ms = int(time.time() * 1000)
    
    # 1. Очистка живой карты (старше 40 мин)
    try:
        r = requests.get(FIREBASE_URL, timeout=8)
        if r.status_code == 200 and r.json():
            data = r.json()
            to_delete = {k: None for k, v in data.items() if isinstance(v, dict) and (now_ms - (v.get("time") or v.get("timestamp") or 0)) > LIFETIME_MS}
            if to_delete:
                base_url = FIREBASE_URL.replace("/points.json", "")
                requests.patch(f"{base_url}/points.json", json=to_delete, timeout=5)
                log(f"🧹 Удалено меток карты старше 40 мин: {len(to_delete)}")
    except Exception as e:
        log(f"Ошибка очистки карты: {e}")

    # 2. Очистка архива статистики (старше 7 дней)
    try:
        r_arch = requests.get(STATS_ARCHIVE_URL, timeout=8)
        if r_arch.status_code == 200 and r_arch.json():
            arch_data = r_arch.json()
            to_delete_arch = {k: None for k, v in arch_data.items() if isinstance(v, dict) and (now_ms - (v.get("time") or 0)) > WEEK_MS}
            if to_delete_arch:
                base_url = FIREBASE_URL.replace("/points.json", "")
                requests.patch(f"{base_url}/stats_archive.json", json=to_delete_arch, timeout=5)
                log(f"🧹 Удалено архивных записей старше 7 дней: {len(to_delete_arch)}")
    except Exception as e:
        log(f"Ошибка очистки архива: {e}")

def get_existing_records():
    try:
        r = requests.get(FIREBASE_URL, timeout=8)
        if r.status_code == 200 and r.json():
            data = r.json()
            records = set()
            for v in data.values():
                if isinstance(v, dict):
                    raw = v.get("raw_text") or v.get("text", "")
                    raw = raw.replace(" (чисто)", "").strip()
                    records.add(raw)
            return records
    except Exception as e:
        log(f"Ошибка базы: {e}")
    return set()

def fetch_tg_posts():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(CHANNEL_URL, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        raw_posts = re.findall(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', r.text, re.DOTALL)
        clean = []
        for p in raw_posts:
            t = re.sub(r'<[^>]+>', '', p).strip()
            if t:
                clean.append(t)
        return clean[-35:]
    except Exception as e:
        log(f"Ошибка загрузки канала: {e}")
        return []

def match_with_knowledge_base(post, kb_list):
    post_low = post.lower()
    for item in kb_list:
        phrase = (item.get("phrase") or "").lower().strip()
        if not phrase:
            continue
        if len(phrase) <= 5:
            if re.search(r'\b' + re.escape(phrase) + r'\b', post_low):
                return item
        else:
            if phrase in post_low:
                return item
    return None

def parse_batch_gemini(posts_list, kb_examples):
    if not posts_list:
        return []

    cleaned_posts = [p.replace('"', "'").replace('\\', '/').replace('\n', ' ').strip() for p in posts_list]
    items = "\n".join([f"[{i}] {p}" for i, p in enumerate(cleaned_posts)])
    url = f"https://generativelanguage.googleapis.com/{API_VERSION}/models/{MODEL_NAME}:generateContent?key={GEMINI_KEY}"
    
    kb_hints = ""
    if kb_examples:
        sample_kb = kb_examples[-8:]
        kb_hints = "ЭТАЛОННЫЕ ПРИМЕРЫ КООРДИНАТ ИЗ БАЗЫ ЗНАНИЙ:\n" + "\n".join([
            f"- \"{k.get('phrase')}\" -> [lat: {k.get('lat')}, lng: {k.get('lng')}], адрес: {k.get('address')}"
            for k in sample_kb if k.get('phrase') and k.get('lat')
        ])

    prompt = f"""Ты эксперт по географии города Днепр (Украина).
Определи точные географические координаты (lat, lng) в пределах Днепра и статус дорожной обстановки для каждого сообщения.

{kb_hints}

ПРИОРИТЕТНЫЕ УЗЛЫ:
1. "Краснозаводская" / "Белелюбского" -> ул. Академика Белелюбского возле ДТРЗ [lat: 48.4812, lng: 34.9940]
2. "Павлова" / "угол Павлова" -> ул. Академика Павлова [lat: 48.4818, lng: 35.0012]
3. "Комбайновый" -> ул. Белелюбского за 2-м поворотом от Павлова [lat: 48.4795, lng: 34.9845]
4. "Водолечебница" -> пр. Свободы, 2 перед Кайдакским мостом [lat: 48.4848, lng: 34.9735]
5. "Кайдакский съезд" / "РОВД" -> съезд с Кайдакского моста [lat: 48.4925, lng: 34.9625]
6. "Речпорт" -> Речной вокзал / Набережная Заводская [lat: 48.4805, lng: 35.0210]
7. "Водокачка" -> Набережная Заводская, район водокачки [lat: 48.4910, lng: 34.9480]
8. "Стан" / "Стан 550" -> Набережная Заводская, район Стана 550 [lat: 48.4865, lng: 34.9850]
9. "Огни" -> Вечный огонь пр. Нигояна [lat: 48.4716, lng: 34.9892]

ПРАВИЛО СТАТУСА:
- danger: бп, б.п., б/п, блокпост, бус, патруль, полиция, тцк, оливки, синие, зеленые, пиксель, проверка, тормозят, пишут, раздают, дождь, тучи, гроза, хмари, 🫒, 🌧️.
- clean: чисто, сухо, пусто, спокойно, ясно, ок, проехал, 👍, 🫡, ✌️, ☀️, 🟢.

Если локация в Днепре — valid: true. Иначе valid: false.

Сообщения:
{items}

Верни строго JSON массив:
[
  {{"id": 0, "valid": true, "address": "Название улицы/ориентира", "lat": 48.4800, "lng": 34.9900, "status": "clean"}},
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

def log_to_stats_archive(now_ms, post, address, lat, lng):
    """Сохранение красной метки в 7-дневный архив для статистики"""
    try:
        archive_entry = {
            "time": now_ms,
            "text": post[:100],
            "address": address,
            "lat": lat,
            "lng": lng
        }
        requests.post(STATS_ARCHIVE_URL, json=archive_entry, timeout=5)
    except Exception as e:
        log(f"Ошибка записи в архив статистики: {e}")

def sync_cycle():
    cleanup_old_points()
    existing_records = get_existing_records()
    kb_list = get_knowledge_base()

    posts = fetch_tg_posts()
    new_posts = [p for p in posts if p not in existing_records]
    log(f"📡 В канале: {len(posts)} | Новых: {len(new_posts)} | В базе знаний: {len(kb_list)}")

    if not new_posts:
        return

    posts_needing_ai = []
    ai_index_map = {}

    for idx, post in enumerate(new_posts):
        matched_kb = match_with_knowledge_base(post, kb_list)
        now_ms = int(time.time() * 1000)

        force_danger = is_danger_text(post)
        has_clean = any(s in post for s in ["👍", "🫡", "✌️", "👌", "☀️", "🟢", "чисто", "спокійно", "ясно", "пусто", "сухо"])
        is_clean = not force_danger and has_clean
        marker_color = "green" if is_clean else "red"
        clean_tag = " (чисто)" if (is_clean and "чисто" not in post.lower()) else ""
        final_text = f"{post}{clean_tag}"

        if matched_kb:
            log(f"  🎯 [БАЗА ЗНАНИЙ]: совпадение '{matched_kb.get('phrase')}' -> {matched_kb.get('address')}")
            payload = {
                "text": final_text,
                "raw_text": post,
                "address": matched_kb.get("address", "Дніпро"),
                "lat": float(matched_kb["lat"]),
                "lng": float(matched_kb["lng"]),
                "status": "чисто" if is_clean else "опасно",
                "color": marker_color,
                "time": now_ms,
                "timestamp": now_ms,
                "from_kb": True
            }
            try:
                requests.post(FIREBASE_URL, json=payload, timeout=8)
                existing_records.add(post)
                if marker_color == "red":
                    log_to_stats_archive(now_ms, post, matched_kb.get("address", "Дніпро"), float(matched_kb["lat"]), float(matched_kb["lng"]))
            except Exception as e:
                log(f"Ошибка сохранения из КБ: {e}")
        else:
            ai_index_map[len(posts_needing_ai)] = post
            posts_needing_ai.append(post)

    if posts_needing_ai:
        results = parse_batch_gemini(posts_needing_ai, kb_list)
        added_count = 0

        for item in results:
            idx = item.get("id")
            if idx is not None and idx in ai_index_map:
                post = ai_index_map[idx]
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

                    marker_color = "green" if is_clean else "red"
                    clean_tag = " (чисто)" if (is_clean and "чисто" not in post.lower()) else ""
                    final_text = f"{post}{clean_tag}"

                    lat_val = float(item["lat"])
                    lng_val = float(item["lng"])
                    addr_val = item.get("address", "Дніпро")

                    payload = {
                        "text": final_text,
                        "raw_text": post,
                        "address": addr_val,
                        "lat": lat_val,
                        "lng": lng_val,
                        "status": "чисто" if is_clean else "опасно",
                        "color": marker_color,
                        "time": now_ms,
                        "timestamp": now_ms
                    }
                    try:
                        r = requests.post(FIREBASE_URL, json=payload, timeout=8)
                        if r.status_code == 200:
                            log(f"  ✅ + {addr_val} ({marker_color})")
                            existing_records.add(post)
                            added_count += 1
                            if marker_color == "red":
                                log_to_stats_archive(now_ms, post, addr_val, lat_val, lng_val)
                    except Exception as e:
                        log(f"Ошибка сохранения: {e}")

        log(f"  🏁 Новых меток через ИИ: {added_count}")

if __name__ == "__main__":
    log("🚀 Запуск непрерывной смены радара 24/7 (305 минут)...")
    for step in range(305):
        sync_cycle()
        if step < 304:
            time.sleep(60)
    log("🏁 Смена успешно завершена, передача следующей смене!")
