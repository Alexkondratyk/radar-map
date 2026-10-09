import os
import re
import json
import time
import requests

GEMINI_KEY = os.environ.get("GEMINI_KEY", "").strip()
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").strip()
TG_URL = "https://t.me/s/dnepr_bez_tck"

if not FIREBASE_URL.endswith(".json"):
    FIREBASE_URL = FIREBASE_URL.rstrip("/") + "/points.json"

def log(msg):
    """Мгновенный вывод сообщения в консоль GitHub."""
    print(msg, flush=True)

def find_active_gemini_model():
    """Скрипт сам спрашивает у Google список доступных моделей и выбирает лучшую."""
    log("🔍 Автопоиск активной модели Gemini для вашего ключа...")
    for api_ver in ["v1beta", "v1"]:
        try:
            url = f"https://generativelanguage.googleapis.com/{api_ver}/models?key={GEMINI_KEY}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("models", [])
                
                # Фильтруем только те, что умеют генерировать текст
                available = [
                    m["name"].replace("models/", "")
                    for m in models
                    if "generateContent" in m.get("supportedGenerationMethods", [])
                ]
                
                log(f"Найдено моделей в {api_ver}: {len(available)}")
                
                # Приоритет быстрых моделей Flash
                for pref in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-flash-8b", "gemini-2.0-flash-exp"]:
                    if pref in available:
                        log(f"✅ Выбрана оптимальная модель: {pref} ({api_ver})")
                        return api_ver, pref
                
                if available:
                    chosen = available[0]
                    log(f"✅ Выбрана доступная модель: {chosen} ({api_ver})")
                    return api_ver, chosen
            else:
                log(f"Проверка {api_ver} вернула код {resp.status_code}: {resp.text[:120]}")
        except Exception as e:
            log(f"Сбой подключения к {api_ver}: {e}")
            
    log("⚠️ Автопоиск не удался, пробуем базовый gemini-2.0-flash в v1beta")
    return "v1beta", "gemini-2.0-flash"

API_VERSION, MODEL_NAME = find_active_gemini_model()

def get_existing_messages():
    """Получает тексты сообщений, которые уже есть в базе."""
    try:
        r = requests.get(FIREBASE_URL, timeout=10)
        if r.status_code == 200 and r.json():
            data = r.json()
            existing = {v.get("text", "").strip() for v in data.values() if isinstance(v, dict)}
            log(f"В базе Firebase уже сохранено точек: {len(existing)}")
            return existing
    except Exception as e:
        log(f"Ошибка проверки базы Firebase: {e}")
    return set()

def fetch_tg_posts():
    """Забирает последние сообщения из открытого веб-зеркала Telegram."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(TG_URL, headers=headers, timeout=10)
        if r.status_code != 200:
            log(f"Ошибка запроса к Telegram: статус {r.status_code}")
            return []
        raw_posts = re.findall(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', r.text, re.DOTALL)
        clean = []
        for p in raw_posts:
            text = re.sub(r'<[^>]+>', '', p).strip()
            if text:
                clean.append(text)
        log(f"Успешно прочитано сообщений из канала: {len(clean)}")
        return clean[-10:]
    except Exception as e:
        log(f"Ошибка загрузки постов: {e}")
        return []

def parse_with_gemini(text):
    """Отправляет пост в выбранную модель Gemini для извлечения координат."""
    url = f"https://generativelanguage.googleapis.com/{API_VERSION}/models/{MODEL_NAME}:generateContent?key={GEMINI_KEY}"
    prompt = f"""Ты картографический аналитик города Днепр (Украина).
Проанализируй текст сообщения: "{text}"
Определи точную локацию/перекресток/район в черте г. Днепр.
Верни строго чистый JSON без markdown:
{{"valid": true, "address": "краткое место", "lat": 48.46, "lng": 35.04}}
Если точной локации в Днепре нет, спам, опрос или реклама — верни строго:
{{"valid": false}}"""

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                raw_text = resp.json()['candidates'][0]['content']['parts'][0]['text']
                # Извлекаем валидный JSON из любого ответа
                match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
            elif resp.status_code in [429, 503]:
                log(f"Временная заминка Google ({resp.status_code}), ждем 4 сек...")
                time.sleep(4)
            else:
                log(f"Ответ Gemini API: код {resp.status_code} - {resp.text[:120]}")
        except Exception as e:
            log(f"Исключение при запросе к Gemini: {e}")
            time.sleep(2)
    return None

def sync_cycle():
    existing = get_existing_messages()
    posts = fetch_tg_posts()

    added = 0
    for post in posts:
        if post in existing:
            continue

        log(f"Обрабатываем пост: {post[:45]}...")
        result = parse_with_gemini(post)

        if result and result.get("valid") and "lat" in result and "lng" in result:
            now_ms = int(time.time() * 1000)
            payload = {
                "text": post,
                "address": result.get("address", "Днепр"),
                "lat": float(result["lat"]),
                "lng": float(result["lng"]),
                "time": now_ms,
                "timestamp": now_ms,
                "source": "dnepr_bez_tck"
            }
            try:
                r = requests.post(FIREBASE_URL, json=payload, timeout=10)
                if r.status_code == 200:
                    log(f"✅ Точка нанесена: {result.get('address')} ({result['lat']}, {result['lng']})")
                    existing.add(post)
                    added += 1
                else:
                    log(f"Ошибка записи в Firebase: код {r.status_code}")
            except Exception as e:
                log(f"Сбой отправки в Firebase: {e}")
        else:
            log("-> Координаты не найдены (не привязано к улице)")

        time.sleep(2)

    log(f"Итог проверки: добавлено новых точек: {added}")

if __name__ == "__main__":
    log("🚀 Старт синхронизации радара (цикл 8 минут с шагом 60 сек)...")
    for step in range(8):
        log(f"--- Проверка {step + 1}/8 ---")
        sync_cycle()
        if step < 7:
            time.sleep(60)
