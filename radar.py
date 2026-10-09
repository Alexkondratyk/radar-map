import os
import re
import json
import time
import datetime
import requests

GEMINI_KEY = os.environ.get("GEMINI_KEY", "").strip()
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").strip()
LIFETIME_MS = 40 * 60 * 1000  # 40 минут

CHANNEL_URL = "https://t.me/s/dnepr_bez_tck"

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
                    log(f"🎯 Модель: {model_name} ({api_ver})")
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
                log(f"🧹 Удалено меток старше 40 мин: {len(to_delete)}")
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

def parse_batch_gemini(posts_list):
    if not posts_list:
        return []

    cleaned_posts = [p.replace('"', "'").replace('\\', '/').replace('\n', ' ').strip() for p in posts_list]
    items = "\n".join([f"[{i}] {p}" for i, p in enumerate(cleaned_posts)])
    url = f"https://generativelanguage.googleapis.com/{API_VERSION}/models/{MODEL_NAME}:generateContent?key={GEMINI_KEY}"
    
    prompt = f"""Ты эксперт по географии города Днепр (Украина).
Определи точные географические координаты (lat, lng) в пределах Днепра и статус дорожной обстановки для каждого сообщения.

ВАЖНО — ОПРЕДЕЛЯЙ ВЕСЬ ГОРОД ДНЕПР:
Наноси любые районы, жилмассивы, улицы, мосты и въезды города:
- Правый берег: Победа (1-6), Тополь (1-3), Сокол, 12-й Квартал, Корея, Мирный, Кротова, Гальченко, Шинная, пр. Богдана Хмельницкого, пр. Александра Поля (Кирова), ул. Рабочая, ул. Криворожская, ул. Титова, пр. Леси Украинки (Пушкина), пр. Науки (Гагарина), Подстанция, Нагорка, Центр, Мост-Сити, Европейская площадь, Вокзал, пр. Сергея Нигояна, пр. Ивана Мазепы, Западный, Диёвка, Парус, Покровский, Красный Камень, Кайдаки.
- Левый берег: Слобожанский проспект, пр. Петра Калнышевского, ул. Калиновая, ул. Янтарная, Образцова, Клочко-6, Березинка, Левобережный (1-3), Донецкое шоссе, Караван, Солнечный, ул. Малиновского, Приднепровск, Игрень, Рыбальск, Самаровка, Подгородное, Слобожанское.
- Мосты: Кайдакский, Амурский (Старый), Центральный (Новый), Южный, Самарский.

ПРИОРИТЕТНЫЕ ОРИЕНТИРЫ:
1. "Краснозаводская" / "Белелюбского" -> ул. Академика Белелюбского возле ДТРЗ [lat: 48.4812, lng: 34.9940]
2. "Павлова" / "угол Павлова" -> ул. Академика Павлова [lat: 48.4818, lng: 35.0012]
3. "Комбайновый" -> ул. Белелюбского за 2-м поворотом от Павлова [lat: 48.4795, lng: 34.9845]
4. "Водолечебница" -> пр. Свободы, 2 перед Кайдакским мостом [lat: 48.4848, lng: 34.9735]
5. "Кайдакский съезд" / "РОВД" -> съезд с Кайдакского моста на ул. Кайдакский Шлях [lat: 48.4925, lng: 34.9625]
6. "Речпорт" -> Речной вокзал / Набережная Заводская [lat: 48.4805, lng: 35.0210]
7. "Водокачка" -> Набережная Заводская, район водокачки [lat: 48.4910, lng: 34.9480]
8. "Стан" / "Стан 550" -> Набережная Заводская, район Стана 550 [lat: 48.4865, lng: 34.9850]
9. "Огни" -> Вечный огонь пр. Нигояна [lat: 48.4716, lng: 34.9892]

ПРАВИЛО СТАТУСА:
- danger: бп, б.п., б/п, блокпост, мобпост, пост, бус, патруль, полиция, тцк, оливки, синие, зеленые, пиксель, проверка, тормозят, пишут, раздают, готовят, дождь, тучи, гроза, хмари, капает, 🫒, 🌧️, ⚡.
- clean: чисто, сухо, пусто, спокойно, ясно, ок, проехал, 👍, 🫡, ✌️, ☀️, 🟢.

Если локация в Днепре — ставь valid: true.
Если текста недостаточно или это не Днепр — ставь valid: false.

Сообщения:
{items}

Верни строго JSON массив:
[
  {{"id": 0, "valid": true, "address": "Название улицы/района", "lat": 48.4600, "lng": 35.0400, "status": "clean"}},
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

    posts = fetch_tg_posts()
    new_posts = [p for p in posts if p not in existing_records]
    log(f"📡 В канале: {len(posts)} | Новых: {len(new_posts)}")

    if not new_posts:
        return

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

                marker_color = "green" if is_clean else "red"
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
                    "timestamp": now_ms
                }
                try:
                    r = requests.post(FIREBASE_URL, json=payload, timeout=8)
                    if r.status_code == 200:
                        log(f"  ✅ + {item.get('address')} ({marker_color})")
                        existing_records.add(post)
                        added_count += 1
                except Exception as e:
                    log(f"Ошибка сохранения: {e}")

    log(f"  🏁 Добавлено новых меток: {added_count}")

if __name__ == "__main__":
    # Определяем длину смены:
    # Запуск в 15:00 и 18:00 (Киев) = смена по 3 часа (185 мин до 18:00 и до 21:00)
    # Запуск в 05:00 и 10:00 (Киев) = смена по 5 часов (305 мин до 10:00 и до 15:00)
    utc_hour = datetime.datetime.now(datetime.timezone.utc).hour

    if utc_hour in [12, 13, 14, 15, 16]:
        shift_minutes = 185  # 3 часа с запасом 5 минут
    else:
        shift_minutes = 305  # 5 часов с запасом 5 минут

    log(f"🚀 Запуск смены радара на {shift_minutes} минут...")
    for step in range(shift_minutes):
        sync_cycle()
        if step < shift_minutes - 1:
            time.sleep(60)
    log("🏁 Смена завершена успешно!")
