import os
import re
import json
import time
from datetime import datetime
import requests

GEMINI_KEY = os.environ.get("GEMINI_KEY", "").strip()
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").strip()
LIFETIME_MS = 40 * 60 * 1000  # 40 минут жизни метки на живой карте
ARCHIVE_RETENTION_MS = 400 * 24 * 60 * 60 * 1000  # 400 дней хранения в архиве статистики

CHANNEL_URL = "https://t.me/s/dnepr_bez_tck"

if FIREBASE_URL and not FIREBASE_URL.endswith(".json"):
    FIREBASE_URL = FIREBASE_URL.rstrip("/") + "/points.json"

KNOWLEDGE_BASE_URL = FIREBASE_URL.replace("/points.json", "/knowledge_base.json") if FIREBASE_URL else ""
STATS_ARCHIVE_URL = FIREBASE_URL.replace("/points.json", "/stats_archive.json") if FIREBASE_URL else ""

def log(msg):
    print(msg, flush=True)

# ==============================================================================
# АВТОПОИСК АКТУАЛЬНОЙ МОДЕЛИ GOOGLE GEMINI
# ==============================================================================
def find_working_model():
    if not GEMINI_KEY:
        log("⚠️ GEMINI_KEY не знайдено!")
        return "models/gemini-2.5-flash"

    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}"
    try:
        r = requests.get(list_url, timeout=10)
        if r.status_code == 200:
            models_list = r.json().get("models", [])
            valid_candidates = []
            for m in models_list:
                methods = m.get("supportedGenerationMethods", [])
                name = m.get("name", "")
                if "generateContent" in methods and name:
                    valid_candidates.append(name)

            # Приоритет актуальных быстрых моделей
            preferred_order = [
                "gemini-3.5-flash-lite",
                "gemini-3.8-flash",
                "gemini-2.5-flash",
                "gemini-2.0-flash",
                "gemini-1.5-flash"
            ]

            def sort_key(name):
                clean = name.replace("models/", "")
                for idx, pref in enumerate(preferred_order):
                    if pref in clean:
                        return idx
                return 99

            sorted_candidates = sorted(valid_candidates, key=sort_key)

            for cand in sorted_candidates:
                test_url = f"https://generativelanguage.googleapis.com/v1beta/{cand}:generateContent?key={GEMINI_KEY}"
                try:
                    test_r = requests.post(
                        test_url,
                        json={"contents": [{"role": "user", "parts": [{"text": "ping"}]}]},
                        timeout=8
                    )
                    if test_r.status_code == 200:
                        log(f"🎯 Обрано модель: {cand}")
                        return cand
                except Exception:
                    continue
    except Exception as e:
        log(f"⚠️ Помилка автовизначення моделі: {e}")

    fallback = "models/gemini-2.5-flash"
    log(f"🎯 Обрано запасну модель: {fallback}")
    return fallback

WORKING_MODEL = find_working_model()

# ==============================================================================
# СЛОВАРЬ СЛЕНГА ЧИСТОТЫ (ДНЕПР)
# ==============================================================================
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

# ==============================================================================
# БАЗА ЗНАНИЙ (С БЕЗОПАСНЫМ ЧТЕНИЕМ КООРДИНАТ)
# ==============================================================================
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
    except Exception as e:
        log(f"⚠️ Помилка завантаження бази знань: {e}")
    return []

def match_knowledge_base(text, kb_list):
    """
    Ищет запись в базе знаний.
    Возвращает объект только если в нём ЕСТЬ корректные координаты.
    """
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

# ==============================================================================
# ОЧИСТКА УСТАРЕВШИХ ТОЧЕК
# ==============================================================================
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
                log(f"🧹 Видалено застарілих міток карти: {len(to_delete)}")
    except Exception as e:
        log(f"⚠️ Помилка очистки карти: {e}")

    try:
        if STATS_ARCHIVE_URL:
            r_arch = requests.get(STATS_ARCHIVE_URL, timeout=8)
            if r_arch.status_code == 200 and r_arch.json():
                arch_data = r_arch.json()
                to_delete_arch = {
                    k: None for k, v in arch_data.items()
                    if isinstance(v, dict) and (now_ms - (v.get("time") or 0)) > ARCHIVE_RETENTION_MS
                }
                if to_delete_arch:
                    requests.patch(f"{base_url}/stats_archive.json", json=to_delete_arch, timeout=8)
                    log(f"🧹 Очищено записів архіву старше 400 днів: {len(to_delete_arch)}")
    except Exception as e:
        log(f"⚠️ Помилка очистки архіву: {e}")

# ==============================================================================
# ПОЛУЧЕНИЕ УЖЕ ОБРАБОТАННЫХ СООБЩЕНИЙ
# ==============================================================================
def get_existing_records():
    records = set()
    if not FIREBASE_URL:
        return records

    try:
        r = requests.get(FIREBASE_URL, timeout=8)
        if r.status_code == 200 and r.json():
            for v in r.json().values():
                if isinstance(v, dict):
                    raw = (v.get("raw_text") or v.get("text") or "").replace(" (чисто)", "").strip()
                    if raw:
                        records.add(raw.lower())
    except Exception:
        pass

    try:
        if STATS_ARCHIVE_URL:
            now_ms = int(time.time() * 1000)
            r_arch = requests.get(STATS_ARCHIVE_URL, timeout=8)
            if r_arch.status_code == 200 and r_arch.json():
                for v in r_arch.json().values():
                    if isinstance(v, dict) and (now_ms - (v.get("time") or 0) < 2 * 3600 * 1000):
                        raw = (v.get("text") or v.get("raw_text") or "").replace(" (чисто)", "").strip()
                        if raw:
                            records.add(raw.lower())
    except Exception:
        pass

    return records

# ==============================================================================
# ПАРСИНГ СООБЩЕНИЙ ИЗ TELEGRAM
# ==============================================================================
def fetch_channel_messages():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(CHANNEL_URL, headers=headers, timeout=12)
        if r.status_code != 200:
            log(f"⚠️ Не вдалося відкрити канал: HTTP {r.status_code}")
            return []

        pattern = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.DOTALL)
        raw_matches = pattern.findall(r.text)

        messages = []
        for m in raw_matches:
            clean = re.sub(r'<br\s*/?>', ' ', m)
            clean = re.sub(r'<[^>]+>', '', clean)
            clean = clean.replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            clean = ' '.join(clean.split()).strip()
            if clean and len(clean) >= 4:
                messages.append(clean)

        return messages
    except Exception as e:
        log(f"⚠️ Помилка зчитування каналу: {e}")
        return []

# ==============================================================================
# ГЕОКОДИНГ ЧЕРЕЗ GEMINI
# ==============================================================================
def parse_with_gemini(text):
    if not GEMINI_KEY:
        return None

    prompt = f"""
Ти — високоточний аналітик геолокації у місті Дніпро (Україна).
Проаналізуй дорожнє повідомлення з Telegram-каналу:
"{text}"

Твоє завдання:
1. Визначити, чи стосується повідомлення конкретного місця / вулиці / перехрестя / орієнтиру в місті Дніпро (або передмісті: Підгородне, Слобожанське, Новоолександрівка).
2. Якщо місце знайдено: визнач точну назву адреси та координати (lat, lng) у Дніпрі (lat близько 48.35 - 48.60, lng близько 34.80 - 35.25).
3. Визнач статус:
   - "чисто": якщо вільно, проїзд спокійний, сонце, 🌞, чисто, знялися, нема нікого.
   - "опасно": якщо блокпост, патруль, зупиняють, сині, хмари, роздають, перевірка.

Відповідай СТРОГО валідним JSON без будь-яких коментарів та без лапок:
{{"found": true, "address": "вул. Робоча", "lat": 48.4501, "lng": 35.0082, "status": "чисто"}}
Якщо локація у Дніпрі відсутня:
{{"found": false}}
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/{WORKING_MODEL}:generateContent?key={GEMINI_KEY}"
    try:
        r = requests.post(
            url,
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}
            },
            timeout=12
        )
        if r.status_code == 200:
            raw_json = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            raw_json = raw_json.replace("```json", "").replace("```", "").strip()
            data = json.loads(raw_json)
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
    except Exception as e:
        log(f"⚠️ Помилка виклику Gemini: {e}")
    return None

# ==============================================================================
# ОСНОВНОЙ ЦИКЛ СИНХРОНИЗАЦИИ
# ==============================================================================
def sync_cycle():
    cleanup_old_points()

    existing_records = get_existing_records()
    kb_list = get_knowledge_base()
    messages = fetch_channel_messages()

    if not messages:
        return

    recent_messages = messages[-25:]
    new_messages = [m for m in recent_messages if m.lower() not in existing_records]

    log(f"📢 В каналі: {len(recent_messages)} | Нових: {len(new_messages)} | В базі знань: {len(kb_list)}")

    if not new_messages:
        return

    now_ms = int(time.time() * 1000)

    for msg in new_messages:
        existing_records.add(msg.lower())
        is_clean = is_message_clean(msg)

        # 1. Поиск в базе знаний (безопасное получение координат)
        matched_kb = match_knowledge_base(msg, kb_list)
        parsed_result = None

        if matched_kb:
            lat_val = matched_kb.get("lat")
            lng_val = matched_kb.get("lng")
            if lat_val is not None and lng_val is not None:
                try:
                    parsed_result = {
                        "address": matched_kb.get("address") or matched_kb.get("phrase") or "Дніпро",
                        "lat": float(lat_val),
                        "lng": float(lng_val),
                        "status": "чисто" if is_clean else "опасно"
                    }
                    log(f"🎯 [БАЗА ЗНАНЬ]: '{matched_kb.get('phrase')}' -> {parsed_result['address']}")
                except (ValueError, TypeError):
                    parsed_result = None

        # 2. Если в базе знаний координаты не найдены — распознаём через Gemini
        if not parsed_result:
            parsed_result = parse_with_gemini(msg)
            if parsed_result:
                log(f"🤖 [GEMINI AI]: '{msg[:40]}...' -> {parsed_result['address']} ({parsed_result['lat']}, {parsed_result['lng']})")

        # 3. Сохранение точки в Firebase
        if parsed_result:
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
                "time": now_ms,
                "timestamp": now_ms
            }

            try:
                requests.post(FIREBASE_URL, json=point_data, timeout=8)
            except Exception as e:
                log(f"⚠️ Помилка запису точки: {e}")

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

# ==============================================================================
# ТОЧКА ВХОДА (РАБОТА В GITHUB ACTIONS)
# ==============================================================================
if __name__ == "__main__":
    log("🚀 Запуск безперервної зміни радара 24/7...")
    start_time = time.time()
    MAX_RUNTIME_SEC = 5 * 3600 + 40 * 60  # Работает до 5 часов 40 минут

    while (time.time() - start_time) < MAX_RUNTIME_SEC:
        try:
            sync_cycle()
        except Exception as e:
            log(f"⚠️ Помилка в циклі: {e}")
        time.sleep(25)
