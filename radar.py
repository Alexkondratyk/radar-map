import os
import re
import json
import time
import random
from datetime import datetime
import requests

GEMINI_KEY = os.environ.get("GEMINI_KEY", "").strip()
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").strip()
LIFETIME_MS = 40 * 60 * 1000  # 40 минут для меток на карте
ARCHIVE_RETENTION_MS = 400 * 24 * 60 * 60 * 1000  # 400 дней для архива аналитики

CHANNEL_BASE_URL = "https://t.me/s/dnepr_bez_tck"

if FIREBASE_URL and not FIREBASE_URL.endswith(".json"):
    FIREBASE_URL = FIREBASE_URL.rstrip("/") + "/points.json"

KNOWLEDGE_BASE_URL = FIREBASE_URL.replace("/points.json", "/knowledge_base.json") if FIREBASE_URL else ""
STATS_ARCHIVE_URL = FIREBASE_URL.replace("/points.json", "/stats_archive.json") if FIREBASE_URL else ""

def log(msg):
    print(msg, flush=True)

PROCESSED_POST_IDS = set()

# ФИЛЬТР РЕКЛАМЫ И АДМИНИСТРАТИВНОГО СПАМА КАНАЛА
AD_FILTER_REGEX = re.compile(
    r'(?:'
    r'напоминаем.*?бот|'
    r'бот\s+с\s+картой|'
    r'бот\s+з\s+картою|'
    r'підтримайте\s+канал|'
    r'поддержите\s+канал|'
    r'канал\s+без\s+тцк|'
    r'збір\s+на|'
    r'сбор\s+на|'
    r'посилання\s+в|'
    r'ссылка\s+в|'
    r'підпишіться|'
    r'подпишитесь|'
    r'реклам[аеу]'
    r')',
    re.IGNORECASE
)

def is_ad_or_spam(text):
    return bool(AD_FILTER_REGEX.search(text))

# ==============================================================================
# ШВИДКИЙ ГЕОКОДЕР ДНІПРА (ЕКСПРЕС-СПИСОК)
# ==============================================================================
LOCAL_STREETS_DB = [
    {"keys": ["гальченк"], "addr": "вул. Василя Гальченка", "lat": 48.3980, "lng": 35.0120},
    {"keys": ["кротов"], "addr": "вул. Бориса Кротова", "lat": 48.3930, "lng": 35.0080},
    {"keys": ["шинн"], "addr": "вул. Шинна", "lat": 48.4210, "lng": 35.0230},
    {"keys": ["12.*квартал", "квартал"], "addr": "12 Квартал", "lat": 48.4050, "lng": 35.0200},
    {"keys": ["топол"], "addr": "ж/м Тополя", "lat": 48.3900, "lng": 35.0350},
    {"keys": ["панікахи", "паникахи"], "addr": "вул. Панікахи", "lat": 48.3950, "lng": 35.0450},
    {"keys": ["космічн", "космическ"], "addr": "вул. Космічна", "lat": 48.4180, "lng": 35.0450},
    {"keys": ["гагарін", "гагарин", "науки"], "addr": "просп. Науки (Гагаріна)", "lat": 48.4350, "lng": 35.0420},
    {"keys": ["казаков", "козаков"], "addr": "вул. Казакова", "lat": 48.4320, "lng": 35.0460},
    {"keys": ["дафі", "дафи", "підстанці", "подстанци"], "addr": "ТРЦ Дафі / Підстанція", "lat": 48.4250, "lng": 35.0220},
    {"keys": ["дну", "універ", "универ"], "addr": "ДНУ (просп. Науки)", "lat": 48.4340, "lng": 35.0430},
    {"keys": ["сікорськ", "сикорск", "тельман"], "addr": "вул. Ігоря Сікорського", "lat": 48.4320, "lng": 35.0120},
    {"keys": ["артем", "січових стрільц", "сечевых стрельц"], "addr": "вул. Січових Стрільців (Артема)", "lat": 48.4550, "lng": 35.0410},
    {"keys": ["поля", "кіров", "киров"], "addr": "просп. Олександра Поля", "lat": 48.4520, "lng": 35.0250},
    {"keys": ["титов"], "addr": "вул. Титова", "lat": 48.4310, "lng": 35.0240},
    {"keys": ["янгел"], "addr": "вул. Академіка Янгеля", "lat": 48.4360, "lng": 35.0090},
    {"keys": ["будівельник", "строител"], "addr": "вул. Будівельників", "lat": 48.4340, "lng": 35.0020},
    {"keys": ["робоч", "рабоч"], "addr": "вул. Робоча", "lat": 48.4501, "lng": 35.0082},
    {"keys": ["шкільн", "школьн"], "addr": "вул. Шкільна", "lat": 48.4410, "lng": 35.0240},
    {"keys": ["савченк"], "addr": "вул. Юрія Савченка", "lat": 48.4580, "lng": 35.0190},
    {"keys": ["лесі українк", "леси украинки", "пушкін", "пушкин"], "addr": "просп. Лесі Українки", "lat": 48.4650, "lng": 35.0220},
    {"keys": ["павлов"], "addr": "вул. Академіка Павлова", "lat": 48.4818, "lng": 35.0012},
    {"keys": ["стан 550", "стан550", " стан "], "addr": "Стан 550 (Набережна Заводська)", "lat": 48.4865, "lng": 34.9850},
    {"keys": ["водокачк"], "addr": "Водокачка (Набережна Заводська)", "lat": 48.4910, "lng": 34.9480},
    {"keys": ["водолікарн", "водолечеб"], "addr": "Водолікарня (просп. Свободи)", "lat": 48.4848, "lng": 34.9735},
    {"keys": ["речпорт", "річпорт", "репорт"], "addr": "Річпорт (Набережна Заводська)", "lat": 48.4805, "lng": 35.0210},
    {"keys": ["парус"], "addr": "ж/м Парус", "lat": 48.4850, "lng": 34.9200},
    {"keys": ["покровськ", "комунар", "коммунар"], "addr": "ж/м Покровський", "lat": 48.4820, "lng": 34.9350},
    {"keys": ["червон.*кам", "красн.*кам"], "addr": "ж/м Червоний Камінь", "lat": 48.4820, "lng": 34.9450},
    {"keys": ["мазеп", "петровськ", "петровск"], "addr": "просп. Івана Мазепи", "lat": 48.4720, "lng": 34.9650},
    {"keys": ["нігоян", "нигоян", "калінін", "калинин"], "addr": "просп. Сергія Нігояна", "lat": 48.4750, "lng": 34.9900},
    {"keys": ["вокзал", "старомостов", "островськ", "островск"], "addr": "Залізничний Вокзал", "lat": 48.4750, "lng": 35.0180},
    {"keys": ["озерк"], "addr": "Ринок Озерка", "lat": 48.4700, "lng": 35.0250},
    {"keys": ["шмідт", "шмидт", "бандер"], "addr": "вул. Степана Бандери (Шмідта)", "lat": 48.4680, "lng": 35.0220},
    {"keys": ["слобожанськ", "правд"], "addr": "просп. Слобожанський", "lat": 48.4950, "lng": 35.0750},
    {"keys": ["калинов"], "addr": "вул. Калинова", "lat": 48.5080, "lng": 35.0600},
    {"keys": ["донецьк.*шосе", "донецк.*шоссе", "караван"], "addr": "Донецьке шосе / Караван", "lat": 48.5350, "lng": 34.9900},
    {"keys": ["сонячн", "солнечн"], "addr": "ж/м Сонячний", "lat": 48.4750, "lng": 35.0650},
    {"keys": ["придніпров", "приднепров"], "addr": "ж/м Придніпровськ", "lat": 48.4050, "lng": 35.1300},
    {"keys": ["підгородн", "подгородн"], "addr": "м. Підгородне", "lat": 48.5750, "lng": 35.1050}
]

def geocode_local(text):
    t = " " + text.lower() + " "
    for item in LOCAL_STREETS_DB:
        for k in item["keys"]:
            pattern = re.compile(k, re.IGNORECASE)
            if pattern.search(t):
                return item["addr"], item["lat"], item["lng"]
    return None, None, None

def geocode_osm_fallback(text):
    clean = re.sub(r'(?:чисто|пусто|вільно|спокійно|бп|блокпост|патруль|роздають|хмари|сині|дощ|сонце|сонечко|🌞|☀️)\b', '', text, flags=re.I).strip()
    words = [w for w in re.split(r'[,+\n/]', clean) if len(w.strip()) >= 4]
    if not words:
        words = [clean]

    for w in words[:2]:
        query = w.strip()
        if len(query) < 4:
            continue
        try:
            url = f"https://nominatim.openstreetmap.org/search?format=json&q={requests.utils.quote(query + ', Дніпро')}&countrycodes=ua&limit=1"
            headers = {"User-Agent": "DniproRadarMap/3.0"}
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data and len(data) > 0:
                    lat = float(data[0]["lat"])
                    lng = float(data[0]["lon"])
                    if 48.25 <= lat <= 48.70 and 34.70 <= lng <= 35.40:
                        name = data[0].get("display_name", query).split(",")[0]
                        return f"вул. {name}", round(lat, 5), round(lng, 5)
        except Exception:
            pass
    return None, None, None

# ТОЛЬКО АКТУАЛЬНЫЕ МОДЕЛИ (1.5, 2.0, 2.5 ИСКЛЮЧЕНЫ)
def find_working_model():
    if not GEMINI_KEY:
        return "models/gemini-3.8-flash"

    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}"
    try:
        r = requests.get(list_url, timeout=10)
        if r.status_code == 200:
            models_list = r.json().get("models", [])
            valid = [
                m.get("name") for m in models_list
                if "generateContent" in m.get("supportedGenerationMethods", []) and m.get("name")
            ]
            preferred = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash"]
            for pref in preferred:
                match = next((v for v in valid if pref in v), None)
                if match:
                    log(f"🎯 Робоча модель Gemini: {match}")
                    return match
    except Exception as e:
        log(f"⚠️ Помилка пошуку моделей: {e}")

    return "models/gemini-3.8-flash"

WORKING_MODEL = find_working_model()

CLEAN_KEYWORDS = [
    "чисто", "чистенько", "пусто", "вільно", "спокійно", "проїзд",
    "без бп", "сонечко", "солнечно", "солнце", "ясно", "сухо",
    "знялися", "пролітайте", "все чисто", "зелено", "🌞", "☀️", "🌤️"
]

def is_message_clean(text):
    t = text.lower()
    if "не чисто" in t or "не пусто" in t or "не вільно" in t:
        return False
    for kw in CLEAN_KEYWORDS:
        if kw in t:
            return True
    return False

def get_knowledge_base():
    if not KNOWLEDGE_BASE_URL:
        return []
    try:
        r = requests.get(f"{KNOWLEDGE_BASE_URL}?t={int(time.time() * 1000)}", timeout=8)
        if r.status_code == 200 and r.json():
            data = r.json()
            if isinstance(data, dict):
                return [v for v in data.values() if isinstance(v, dict)]
            elif isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
    except Exception:
        pass
    return []

def match_knowledge_base(text, kb_list):
    t = text.lower()
    for item in kb_list:
        phrase = item.get("phrase", "").strip().lower()
        if phrase and len(phrase) >= 3 and phrase in t:
            lat = item.get("lat")
            lng = item.get("lng")
            if lat is not None and lng is not None:
                try:
                    float(lat)
                    float(lng)
                    return item
                except (ValueError, TypeError):
                    pass
    return None

def cleanup_old_points():
    now_ms = int(time.time() * 1000)
    if not FIREBASE_URL:
        return
    base_url = FIREBASE_URL.replace("/points.json", "")

    try:
        r = requests.get(FIREBASE_URL, timeout=8)
        if r.status_code == 200 and r.json():
            data = r.json()
            to_delete = {
                k: None for k, v in data.items()
                if isinstance(v, dict) and (now_ms - (v.get("time") or v.get("timestamp") or 0)) > LIFETIME_MS
            }
            if to_delete:
                requests.patch(f"{base_url}/points.json", json=to_delete, timeout=8)
                log(f"🧹 Видалено застарілих міток: {len(to_delete)}")
    except Exception:
        pass

def fetch_channel_messages():
    timestamp_param = int(time.time())
    url = f"{CHANNEL_BASE_URL}?t={timestamp_param}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }

    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            return []

        pattern = re.compile(
            r'data-post="[^/]+/(\d+)"[^>]*>.*?<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            re.DOTALL
        )
        matches = pattern.findall(r.text)

        results = []
        for post_id, raw_html in matches:
            clean = re.sub(r'<br\s*/?>', '\n', raw_html)
            clean = re.sub(r'<[^>]+>', '', clean)
            clean = clean.replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').strip()

            lines = [l.strip() for l in clean.split('\n') if len(l.strip()) >= 3]
            for line in lines:
                results.append((post_id, line))

        return results
    except Exception as e:
        log(f"⚠️ Помилка зчитування Telegram: {e}")
        return []

def parse_with_gemini(text):
    if not GEMINI_KEY:
        return None

    prompt = f"""
Ти — аналітик геолокації у місті Дніпро (Україна).
Повідомлення: "{text}"
Знайди назву вулиці або орієнтир у місті Дніпро. Зверни увагу на скорочення та прізвища (наприклад "Гальченко" = вул. Василя Гальченка).
Відповідай ТІЛЬКИ JSON:
{{"found": true, "address": "вул. ...", "lat": 48.45, "lng": 35.01, "status": "чисто"|"опасно"}}
Якщо локації немає: {{"found": false}}
"""

    models_to_try = [WORKING_MODEL, "models/gemini-3.5-flash-lite", "models/gemini-3.5-flash"]
    for m in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/{m}:generateContent?key={GEMINI_KEY}"
        try:
            r = requests.post(
                url,
                json={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}
                },
                timeout=10
            )
            if r.status_code == 200:
                raw = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                raw = raw.replace("```json", "").replace("```", "").strip()
                data = json.loads(raw)
                if data.get("found") and data.get("lat") and data.get("lng"):
                    lat = float(data["lat"])
                    lng = float(data["lng"])
                    if 48.25 <= lat <= 48.70 and 34.70 <= lng <= 35.40:
                        return {
                            "address": data.get("address", "Дніпро"),
                            "lat": round(lat, 5),
                            "lng": round(lng, 5),
                            "status": data.get("status", "опасно")
                        }
                return None
        except Exception:
            continue
    return None

# ==============================================================================
# ЦИКЛ СИНХРОНИЗАЦИИ (БЕЗ ОТСЕИВАНИЯ: НЕИЗВЕСТНЫЕ ИДУТ В ЦЕНТР НА ОБУЧЕНИЕ)
# ==============================================================================
def sync_cycle():
    global PROCESSED_POST_IDS
    cleanup_old_points()

    kb_list = get_knowledge_base()
    items = fetch_channel_messages()

    if not items:
        return

    new_items = []
    for post_id, text in items:
        unique_key = f"{post_id}_{text.lower()}"
        if unique_key not in PROCESSED_POST_IDS:
            new_items.append((unique_key, text))

    log(f"📢 В каналі: {len(items)} | Нових: {len(new_items)} | В базі знань: {len(kb_list)}")

    if not new_items:
        return

    now_ms = int(time.time() * 1000)

    for unique_key, msg in new_items:
        PROCESSED_POST_IDS.add(unique_key)

        # 1. Отсеиваем рекламу и админ-спам
        if is_ad_or_spam(msg):
            log(f"🚫 [РЕКЛАМА/СПАМ ПРОПУЩЕНО]: '{msg[:45]}'")
            continue

        is_clean = is_message_clean(msg)
        parsed_result = None

        # 2. Проверяем базу знаний
        matched_kb = match_knowledge_base(msg, kb_list)
        if matched_kb and matched_kb.get("lat") and matched_kb.get("lng"):
            parsed_result = {
                "address": matched_kb.get("address") or matched_kb.get("phrase"),
                "lat": float(matched_kb["lat"]),
                "lng": float(matched_kb["lng"]),
                "status": "чисто" if is_clean else "опасно",
                "needs_training": False
            }
            log(f"🎯 [БАЗА ЗНАНЬ]: '{matched_kb.get('phrase')}' ➔ {parsed_result['address']}")

        # 3. Экспресс-геокодер Днепра
        if not parsed_result:
            addr, lat, lng = geocode_local(msg)
            if addr and lat and lng:
                parsed_result = {
                    "address": addr,
                    "lat": lat,
                    "lng": lng,
                    "status": "чисто" if is_clean else "опасно",
                    "needs_training": False
                }
                log(f"⚡ [ЛОКАЛЬНО]: '{msg[:45]}' ➔ {addr}")

        # 4. Резерв OpenStreetMap
        if not parsed_result:
            osm_addr, osm_lat, osm_lng = geocode_osm_fallback(msg)
            if osm_addr and osm_lat and osm_lng:
                parsed_result = {
                    "address": osm_addr,
                    "lat": osm_lat,
                    "lng": osm_lng,
                    "status": "чисто" if is_clean else "опасно",
                    "needs_training": False
                }
                log(f"🗺️ [OSM КАРТА]: '{msg[:45]}' ➔ {osm_addr}")

        # 5. Gemini AI
        if not parsed_result:
            time.sleep(1.0)
            ai_res = parse_with_gemini(msg)
            if ai_res:
                parsed_result = {**ai_res, "needs_training": False}
                log(f"🤖 [GEMINI AI]: '{msg[:45]}' ➔ {parsed_result['address']}")

        # 6. ЕСЛИ АДРЕС НЕ НАЙДЕН — НЕ ВЫБРАСЫВАЕМ! СТАВИМ В ЦЕНТР НА ОБУЧЕНИЕ
        if not parsed_result:
            center_lat = round(48.4645 + random.uniform(-0.0035, 0.0035), 5)
            center_lng = round(35.0465 + random.uniform(-0.0035, 0.0035), 5)
            parsed_result = {
                "address": "Потребує навчання (перетягніть на потрібну вулицю)",
                "lat": center_lat,
                "lng": center_lng,
                "status": "чисто" if is_clean else "опасно",
                "needs_training": True
            }
            log(f"📍 [НАВЧАННЯ]: Невідому точку розміщено в центрі для вас: '{msg[:45]}'")

        # 7. Сохранение точки в Firebase
        status_clean = (parsed_result["status"] == "чисто" or is_clean)
        color = "green" if status_clean else "red"
        display_text = msg
        if status_clean and "чисто" not in msg.lower():
            display_text = f"{msg} (чисто)"

        point_data = {
            "text": display_text,
            "raw_text": msg,
            "address": parsed_result["address"],
            "lat": parsed_result["lat"],
            "lng": parsed_result["lng"],
            "color": color,
            "status": "чисто" if status_clean else "опасно",
            "is_clean": status_clean,
            "needs_training": parsed_result.get("needs_training", False),
            "time": now_ms,
            "timestamp": now_ms
        }

        try:
            requests.post(FIREBASE_URL, json=point_data, timeout=8)
        except Exception as e:
            log(f"⚠️ Помилка Firebase: {e}")

        if STATS_ARCHIVE_URL:
            try:
                archive_entry = {
                    "text": msg,
                    "address": parsed_result["address"],
                    "lat": parsed_result["lat"],
                    "lng": parsed_result["lng"],
                    "is_clean": status_clean,
                    "time": now_ms
                }
                requests.post(STATS_ARCHIVE_URL, json=archive_entry, timeout=8)
            except Exception:
                pass

if __name__ == "__main__":
    log("🚀 Запуск безперервної зміни радара 24/7...")
    start_time = time.time()
    MAX_RUNTIME_SEC = 5 * 3600 + 40 * 60

    while (time.time() - start_time) < MAX_RUNTIME_SEC:
        try:
            sync_cycle()
        except Exception as e:
            log(f"⚠️ Помилка в циклі: {e}")
        time.sleep(20)
